
import os
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)
from dotenv import load_dotenv
from werkzeug.security import check_password_hash
from mysql.connector import IntegrityError

from app.database import get_db_connection


load_dotenv()

app = Flask(
    __name__,
    template_folder="app/templates",
    static_folder="app/static",
)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    raise RuntimeError("Please configure SECRET_KEY in your .env file.")


# =========================
# HELPER FUNCTIONS
# =========================

def login_required():
    return "user_id" in session


def get_branches():
    """Return registered branches in alphabetical order."""
    connection = get_db_connection()

    if connection is None:
        return []

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, branch_name
            FROM branches
            ORDER BY branch_name ASC
        """)
        return cursor.fetchall()

    except Exception:
        app.logger.exception("Failed to retrieve branches.")
        return []

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


# =========================
# HOME AND AUTHENTICATION
# =========================

@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter your email and password.", "danger")
            return render_template("auth/login.html")

        connection = get_db_connection()

        if connection is None:
            flash("Unable to connect to the database.", "danger")
            return render_template("auth/login.html")

        cursor = None

        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, firstname, lastname, email, password
                FROM users
                WHERE email = %s
                LIMIT 1
            """, (email,))

            user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = (
                    f'{user["firstname"]} {user["lastname"]}'
                )
                session["user_email"] = user["email"]

                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "danger")

        except Exception:
            app.logger.exception("Login operation failed.")
            flash("Something went wrong. Please try again.", "danger")

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    return render_template("auth/login.html")


@app.route("/dashboard")
def dashboard():
    if not login_required():
        flash("Please sign in to access the dashboard.", "warning")
        return redirect(url_for("login"))

    stats = {
        "total_sales": 0,
        "total_banked": 0,
        "total_variance": 0,
        "open_exceptions": 0,
        "period_label": "Current reporting period",
    }

    attention_branches = []

    return render_template(
        "dashboard/index.html",
        stats=stats,
        attention_branches=attention_branches,
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


# =========================
# BRANCH MANAGEMENT
# =========================

@app.route("/branches")
def branches():
    if not login_required():
        return redirect(url_for("login"))

    connection = get_db_connection()

    if connection is None:
        flash("Unable to connect to the database.", "danger")
        return render_template("branches/index.html", branches=[])

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, branch_name, created_at
            FROM branches
            ORDER BY branch_name ASC
        """)

        branch_list = cursor.fetchall()

        return render_template(
            "branches/index.html",
            branches=branch_list,
        )

    except Exception:
        app.logger.exception("Failed to load branches.")
        flash("Unable to load branches.", "danger")
        return render_template("branches/index.html", branches=[])

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


# =========================
# SALES MANAGEMENT
# =========================

@app.route("/sales", methods=["GET", "POST"])
def sales():
    if not login_required():
        return redirect(url_for("login"))

    allowed_payment_types = {
        "MPESA",
        "CASH",
    }

    if request.method == "POST":
        branch_id = request.form.get("branch_id", "").strip()
        sale_date_text = request.form.get("sale_date", "").strip()
        ticket_text = request.form.get("ticket_sales", "0").strip()
        parcel_text = request.form.get("parcel_sales", "0").strip()
        payment_type = request.form.get("payment_type", "").strip()

        try:
            if not branch_id or not sale_date_text:
                raise ValueError("Please select a branch and sales date.")

            branch_id = int(branch_id)
            parsed_date = date.fromisoformat(sale_date_text)

            if parsed_date > date.today():
                raise ValueError("Sales date cannot be in the future.")

            if payment_type not in allowed_payment_types:
                raise ValueError("Please select a valid payment type.")

            ticket_amount = Decimal(ticket_text or "0")
            parcel_amount = Decimal(parcel_text or "0")

            if not ticket_amount.is_finite() or not parcel_amount.is_finite():
                raise ValueError("Enter valid sales amounts.")

            if ticket_amount < 0 or parcel_amount < 0:
                raise ValueError("Sales amounts cannot be negative.")

            max_amount = Decimal("9999999999999.99")

            if ticket_amount > max_amount or parcel_amount > max_amount:
                raise ValueError("The entered sales amount is too large.")

            # Ensure amounts have no more than two decimal places.
            if (
                ticket_amount != ticket_amount.quantize(Decimal("0.01"))
                or parcel_amount != parcel_amount.quantize(Decimal("0.01"))
            ):
                raise ValueError("Sales amounts can have at most two decimal places.")

            if ticket_amount == 0 and parcel_amount == 0:
                raise ValueError(
                    "Enter ticket revenue, parcel revenue, or both."
                )

        except (ValueError, InvalidOperation):
            flash(
                "Please check the branch, date, payment type, "
                "and sales amounts.",
                "danger",
            )
            return redirect(url_for("sales"))

        connection = get_db_connection()

        if connection is None:
            flash("Unable to connect to the database.", "danger")
            return redirect(url_for("sales"))

        cursor = None

        try:
            cursor = connection.cursor(dictionary=True)

            # Confirm the selected branch exists.
            cursor.execute(
                "SELECT id FROM branches WHERE id = %s",
                (branch_id,),
            )

            if cursor.fetchone() is None:
                raise ValueError("The selected branch does not exist.")

            requested_sales = []

            if ticket_amount > 0:
                requested_sales.append(("Ticket", ticket_amount))

            if parcel_amount > 0:
                requested_sales.append(("Parcel", parcel_amount))

            # Reject duplicate sale types for this branch and date.
            cursor.execute("""
                SELECT LOWER(sale_type) AS sale_type
                FROM sales
                WHERE branch_id = %s
                  AND sale_date = %s
                  AND LOWER(sale_type) IN ('ticket', 'parcel')
            """, (branch_id, parsed_date))

            existing_types = {
                row["sale_type"] for row in cursor.fetchall()
            }

            duplicate_types = [
                sale_type
                for sale_type, amount in requested_sales
                if sale_type.lower() in existing_types
            ]

            if duplicate_types:
                raise ValueError(
                    "A record already exists for this branch, date, "
                    "and sale type: "
                    + ", ".join(duplicate_types)
                    + ". Please review the existing records."
                )

            # Both records are saved in one transaction.
            for sale_type, amount in requested_sales:
                cursor.execute("""
                    INSERT INTO sales
                        (
                            branch_id,
                            sale_type,
                            total_sales,
                            sale_date,
                            payment_type
                        )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    branch_id,
                    sale_type,
                    amount,
                    parsed_date,
                    payment_type,
                ))

            connection.commit()

            total_sales = ticket_amount + parcel_amount

            flash(
                f"Sales saved successfully. Combined total: "
                f"KES {total_sales:,.2f}.",
                "success",
            )

        except ValueError as error:
            connection.rollback()
            flash(str(error), "danger")

        except IntegrityError:
            connection.rollback()
            flash(
                "A duplicate sales record was detected. "
                "Please refresh and review existing records.",
                "warning",
            )

        except Exception:
            connection.rollback()
            app.logger.exception("Failed to save sales.")
            flash("Unable to save sales. Please try again.", "danger")

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

        return redirect(url_for("sales"))

    # GET: Load branches and sales history.
    connection = get_db_connection()

    if connection is None:
        flash("Unable to connect to the database.", "danger")

        return render_template(
            "sales/index.html",
            branches=[],
            sales_records=[],
            total_sales=Decimal("0.00"),
            today=date.today().isoformat(),
        )

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, branch_name
            FROM branches
            ORDER BY branch_name ASC
        """)
        branch_list = cursor.fetchall()

        cursor.execute("""
            SELECT
                s.id,
                s.branch_id,
                b.branch_name,
                s.sale_type,
                s.payment_type,
                s.total_sales,
                s.sale_date,
                s.created_at
            FROM sales AS s
            INNER JOIN branches AS b ON b.id = s.branch_id
            ORDER BY s.sale_date DESC, s.id DESC
        """)
        sales_records = cursor.fetchall()

        # Combined total of all recorded sales.
        cursor.execute("""
            SELECT COALESCE(SUM(total_sales), 0) AS total_sales
            FROM sales
        """)
        total_result = cursor.fetchone()
        total_sales = total_result["total_sales"]

        return render_template(
            "sales/index.html",
            branches=branch_list,
            sales_records=sales_records,
            total_sales=total_sales,
            today=date.today().isoformat(),
        )

    except Exception:
        app.logger.exception("Failed to load sales records.")
        flash("Unable to load sales records.", "danger")

        return render_template(
            "sales/index.html",
            branches=[],
            sales_records=[],
            total_sales=Decimal("0.00"),
            today=date.today().isoformat(),
        )

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


# =========================
# OTHER OPERATIONS
# These remain placeholders.
# =========================

def render_operation_page(page_key, title, subtitle):
    return render_template(
        "operations/page.html",
        page_key=page_key,
        title=title,
        subtitle=subtitle,
        today=date.today().isoformat(),
        branches=get_branches(),
    )


@app.route("/banking", methods=["GET", "POST"])
def banking():
    if not login_required():
        return redirect(url_for("login"))

    if request.method == "POST":
        flash(
            "Banking form received. Database saving has not "
            "been implemented yet.",
            "info",
        )
        return redirect(url_for("banking"))

    return render_operation_page(
        "banking",
        "Banking Management",
        "Record revenue deposited or remitted by branches.",
    )


@app.route("/reconciliation", methods=["GET", "POST"])
def reconciliation():
    if not login_required():
        return redirect(url_for("login"))

    if request.method == "POST":
        flash(
            "Reconciliation request received. Database processing "
            "has not been implemented yet.",
            "info",
        )
        return redirect(url_for("reconciliation"))

    return render_operation_page(
        "reconciliation",
        "Revenue Reconciliation",
        "Compare expected revenue against the amount banked.",
    )


@app.route("/exceptions")
def exceptions():
    if not login_required():
        return redirect(url_for("login"))

    return render_operation_page(
        "exceptions",
        "Exception Management",
        "Review and follow up on revenue discrepancies.",
    )


@app.route("/reports", methods=["GET", "POST"])
def reports():
    if not login_required():
        return redirect(url_for("login"))

    if request.method == "POST":
        flash(
            "Report filters received. Report generation "
            "has not been implemented yet.",
            "info",
        )
        return redirect(url_for("reports"))

    return render_operation_page(
        "reports",
        "Revenue Reports",
        "Review revenue, banking, and reconciliation performance.",
    )


if __name__ == "__main__":
    app.run(debug=True)

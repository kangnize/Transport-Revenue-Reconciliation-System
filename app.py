
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


# Replace the existing placeholder `banking()` route in app.py with this.
# Written for the existing table `banking`:
#   id, branch_id, banking_amount, banking_date, reference_number,
#   payment_method, created_by, created_at
# If your table has a different name, change it in the three queries below.
# No new imports are needed: date, Decimal, InvalidOperation, IntegrityError,
# session, flash, redirect, url_for and get_db_connection are already imported.

# While setting this page up, show the real database error on screen.
# Set to False once banking saves correctly.
SHOW_DB_ERRORS = True


def _empty_banking_context():
    return {
        "positions": [],
        "banking_records": [],
        "total_sales": Decimal("0.00"),
        "total_banked": Decimal("0.00"),
        "outstanding": Decimal("0.00"),
        "banked_pct": 0,
        "today": date.today().isoformat(),
    }


@app.route("/banking", methods=["GET", "POST"])
def banking():
    if not login_required():
        return redirect(url_for("login"))

    allowed_payment_types = {"MPESA", "CASH"}

    if request.method == "POST":
        app.logger.info("Banking POST received: %s", dict(request.form))
        branch_id = request.form.get("branch_id", "").strip()
        bank_date_text = request.form.get("bank_date", "").strip()
        payment_type = request.form.get("payment_type", "").strip()
        amount_text = request.form.get("amount", "").strip()
        reference = request.form.get("reference", "").strip()

        try:
            if not branch_id or not bank_date_text:
                raise ValueError("Please select a branch and banking date.")

            branch_id = int(branch_id)
            parsed_date = date.fromisoformat(bank_date_text)

            if parsed_date > date.today():
                raise ValueError("Banking date cannot be in the future.")

            if payment_type not in allowed_payment_types:
                raise ValueError("Please select a valid payment type.")

            if not reference:
                raise ValueError("Enter the bank slip number or M-PESA code.")

            if len(reference) > 100:
                raise ValueError("The reference can have at most 100 characters.")

            amount = Decimal(amount_text or "0")

            if not amount.is_finite():
                raise ValueError("Enter a valid amount.")

            if amount <= 0:
                raise ValueError("The amount banked must be greater than zero.")

            if amount > Decimal("9999999999999.99"):
                raise ValueError("The entered amount is too large.")

            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError("The amount can have at most two decimal places.")

        except InvalidOperation:
            flash("Enter a valid amount.", "danger")
            return redirect(url_for("banking"))

        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("banking"))

        connection = get_db_connection()

        if connection is None:
            flash("Unable to connect to the database.", "danger")
            return redirect(url_for("banking"))

        cursor = None

        try:
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                "SELECT id FROM branches WHERE id = %s",
                (branch_id,),
            )

            if cursor.fetchone() is None:
                raise ValueError("The selected branch does not exist.")

            # reference_number is unique across the whole table.
            cursor.execute("""
                SELECT k.id, b.branch_name
                FROM banking AS k
                INNER JOIN branches AS b ON b.id = k.branch_id
                WHERE k.reference_number = %s
                LIMIT 1
            """, (reference,))
            existing = cursor.fetchone()

            if existing is not None:
                raise ValueError(
                    f"The reference {reference} has already been recorded "
                    f"(for {existing['branch_name']}). Each reference can "
                    f"only be used once."
                )

            cursor.execute("""
                INSERT INTO banking
                    (branch_id, banking_amount, banking_date,
                     reference_number, payment_method, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                branch_id,
                amount,
                parsed_date,
                reference,
                payment_type,
                session.get("user_id"),
            ))

            connection.commit()

            # Tell the user where the branch stands after this deposit.
            cursor.execute("""
                SELECT
                    b.branch_name,
                    COALESCE((
                        SELECT SUM(s.total_sales)
                        FROM sales AS s WHERE s.branch_id = b.id
                    ), 0) AS sales_total,
                    COALESCE((
                        SELECT SUM(k.banking_amount)
                        FROM banking AS k WHERE k.branch_id = b.id
                    ), 0) AS banked_total
                FROM branches AS b
                WHERE b.id = %s
            """, (branch_id,))
            result = cursor.fetchone()

            message = f"Banking saved. Amount: KES {amount:,.2f}."
            category = "success"

            if result:
                left = Decimal(result["sales_total"]) - Decimal(result["banked_total"])
                name = result["branch_name"]

                if left > 0:
                    message += (
                        f" {name} still has KES {left:,.2f} to bank."
                    )
                elif left == 0:
                    message += (
                        f" {name} has banked everything it sold."
                    )
                else:
                    message += (
                        f" {name} has banked KES {-left:,.2f} more than "
                        f"its recorded sales. Please check the amounts."
                    )
                    category = "warning"

            flash(message, category)

        except ValueError as error:
            connection.rollback()
            flash(str(error), "danger")

        except IntegrityError as error:
            connection.rollback()
            app.logger.exception("Integrity error saving banking record.")

            if error.errno == 1062:
                flash(
                    "This reference has already been recorded. "
                    "Each reference can only be used once.",
                    "warning",
                )
            else:
                message = (
                    "The deposit could not be saved because it conflicts "
                    "with related records."
                )
                if SHOW_DB_ERRORS:
                    message += f" Details: {error}"
                flash(message, "danger")

        except Exception as error:
            connection.rollback()
            app.logger.exception("Failed to save banking record.")
            message = "Unable to save banking. Please try again."
            if SHOW_DB_ERRORS:
                message += f" Details: {error}"
            flash(message, "danger")

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

        return redirect(url_for("banking"))

    # GET: branch positions (sold vs banked) and deposit history.
    connection = get_db_connection()

    if connection is None:
        flash("Unable to connect to the database.", "danger")
        return render_template("banking/index.html", **_empty_banking_context())

    cursor = None
    context = _empty_banking_context()

    try:
        cursor = connection.cursor(dictionary=True)

        # 1) Branches first, so the dropdown always works.
        cursor.execute("""
            SELECT id, branch_name
            FROM branches
            ORDER BY branch_name ASC
        """)
        branch_rows = cursor.fetchall()

        # 2) Sales totals per branch.
        sales_by_branch = {}
        try:
            cursor.execute("""
                SELECT branch_id, SUM(total_sales) AS total
                FROM sales
                GROUP BY branch_id
            """)
            for row in cursor.fetchall():
                sales_by_branch[row["branch_id"]] = Decimal(row["total"] or 0)
        except Exception:
            app.logger.exception("Failed to load sales totals for banking.")
            flash("Sales totals could not be loaded.", "warning")

        # 3) Banked totals and deposit history. A problem here (for example
        #    a different table name) must not empty the branch list.
        banked_by_branch = {}
        banking_records = []
        try:
            cursor.execute("""
                SELECT branch_id, SUM(banking_amount) AS total
                FROM banking
                GROUP BY branch_id
            """)
            for row in cursor.fetchall():
                banked_by_branch[row["branch_id"]] = Decimal(row["total"] or 0)

            cursor.execute("""
                SELECT
                    k.id,
                    b.branch_name,
                    k.banking_date     AS bank_date,
                    k.payment_method   AS payment_type,
                    k.banking_amount   AS amount,
                    k.reference_number AS reference
                FROM banking AS k
                INNER JOIN branches AS b ON b.id = k.branch_id
                ORDER BY k.banking_date DESC, k.id DESC
            """)
            banking_records = cursor.fetchall()
        except Exception as error:
            app.logger.exception("Failed to load banking records.")
            message = "Banking records could not be loaded."
            if SHOW_DB_ERRORS:
                message += f" Details: {error}"
            flash(message, "warning")

        positions = []
        total_sales = Decimal("0.00")
        total_banked = Decimal("0.00")

        for branch in branch_rows:
            sales_total = sales_by_branch.get(branch["id"], Decimal("0.00"))
            banked_total = banked_by_branch.get(branch["id"], Decimal("0.00"))
            outstanding = sales_total - banked_total

            if outstanding == 0:
                status = "cleared"
            elif outstanding > 0:
                status = "outstanding"
            else:
                status = "over"

            positions.append({
                "id": branch["id"],
                "branch_name": branch["branch_name"],
                "sales_total": sales_total,
                "banked_total": banked_total,
                "outstanding": outstanding,
                "status": status,
            })

            total_sales += sales_total
            total_banked += banked_total

        banked_pct = (
            float(total_banked / total_sales * 100) if total_sales > 0 else 0
        )

        return render_template(
            "banking/index.html",
            positions=positions,
            banking_records=banking_records,
            total_sales=total_sales,
            total_banked=total_banked,
            outstanding=total_sales - total_banked,
            banked_pct=min(banked_pct, 100),
            today=date.today().isoformat(),
        )

    except Exception:
        app.logger.exception("Failed to load the banking page.")
        flash("Unable to load the banking page.", "danger")
        return render_template("banking/index.html", **context)

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


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

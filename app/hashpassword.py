
from werkzeug.security import generate_password_hash
from getpass import getpass


def main():
    password = getpass("Admin123!")

    if not password:
        print("Error: Password cannot be empty.")
        return

    hashed_password = generate_password_hash(password)

    print("\nPassword hash generated successfully:")
    print(hashed_password)


if __name__ == "__main__":
    main()

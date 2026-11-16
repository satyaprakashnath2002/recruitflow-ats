import argparse
import getpass
import sys

from pydantic import TypeAdapter, ValidationError, EmailStr
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.auth import Employee, EmployeeRole, EmployeeStatus


def create_initial_admin() -> int:
    full_name = input("Initial Admin full name: ").strip()
    if not full_name or len(full_name) > 200:
        print("A full name of 1 to 200 characters is required.", file=sys.stderr)
        return 2
    email_value = input("Initial Admin email: ").strip()
    try:
        email = TypeAdapter(EmailStr).validate_python(email_value).lower()
    except ValidationError:
        print("A valid email address is required.", file=sys.stderr)
        return 2

    password = getpass.getpass("Initial Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    minimum = get_settings().password_min_length
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    if len(password) < minimum:
        print(f"Password must contain at least {minimum} characters.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        if db.scalar(select(Employee.id).where(Employee.role == EmployeeRole.ADMIN)) is not None:
            print("An Admin account already exists; refusing to create another bootstrap account.", file=sys.stderr)
            return 1
        if db.scalar(select(Employee.id).where(Employee.email == email)) is not None:
            print("An employee with this email already exists.", file=sys.stderr)
            return 1
        db.add(
            Employee(
                full_name=full_name,
                email=email,
                role=EmployeeRole.ADMIN,
                status=EmployeeStatus.ACTIVE,
                password_hash=hash_password(password),
            )
        )
        db.commit()
    print(f"Initial Admin account created for {email}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["create-admin", "process-notifications"])
    args = parser.parse_args()
    if args.command == "create-admin":
        raise SystemExit(create_initial_admin())
    from app.services.notifications import process_due_reminders
    from app.services.offers import process_due_offer_reminders

    with SessionLocal() as db:
        interview_count = process_due_reminders(db)
    with SessionLocal() as db:
        offer_count = process_due_offer_reminders(db)
    print(f"Processed {interview_count} interview reminder(s) and {offer_count} offer reminder/expiry item(s).")


if __name__ == "__main__":
    main()

from app import DATABASE_PATH, clear_database


def main() -> None:
    clear_database(clear_users=True)
    print(f"Blind Lunch database cleared: {DATABASE_PATH}")


if __name__ == "__main__":
    main()

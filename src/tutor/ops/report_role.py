"""SQL for the gate report's least-privilege role (run by the deployment migrate step).

Print it with `uv run just report-role-sql` and run it as a superuser (only a superuser may
create a BYPASSRLS role). It is idempotent and holds no password: the deployment sets one from
a secret afterwards, e.g. `ALTER ROLE tutor_report PASSWORD :'report_password'` in psql with the
secret passed as a variable (never written to the repo or a command line).

The role may log in, read three tables and nothing else. BYPASSRLS is needed because those tables
have FORCE ROW LEVEL SECURITY keyed on the app's per-user setting; the report counts across users.
"""

REPORT_ROLE = "tutor_report"

REPORT_ROLE_SQL = """\
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tutor_report') THEN
        CREATE ROLE tutor_report LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
    END IF;
END
$$;
ALTER ROLE tutor_report LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
ALTER ROLE tutor_report SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO tutor_report;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM tutor_report;
GRANT SELECT ON sessions, session_metrics, audit_log TO tutor_report;
"""


def main() -> int:
    print(REPORT_ROLE_SQL, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

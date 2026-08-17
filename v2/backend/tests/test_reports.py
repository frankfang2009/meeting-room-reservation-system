from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.test_backend import BackendTestCase


class ReportApiTests(BackendTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_system()
        self.login()
        self.admin = self.bootstrap()["currentUser"]
        self.employee = self.add_employee()
        self.employee_client = self.app.test_client()
        self.login("employee", "employee-pass-123", client=self.employee_client)
        rooms = self.bootstrap()["rooms"]
        self.room_one = rooms[0]
        self.room_two = rooms[1]

        self.employee_active_one = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                self.room_one["id"],
                start="09:00",
                partyName="=2+2",
                caseNumber="-10",
                purpose="@事项",
                notes="+SUM(A1:A2)",
                tagId="tag-3",
            ),
            client=self.employee_client,
        ).get_json()
        self.employee_active_two = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                self.room_one["id"],
                start="10:00",
                duration=90,
                caseNumber="EMP-002",
                tagId="tag-1",
            ),
            client=self.employee_client,
        ).get_json()
        cancelled = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                self.room_one["id"],
                start="11:30",
                caseNumber="EMP-CANCELLED",
                tagId="tag-2",
            ),
            client=self.employee_client,
        ).get_json()
        self.write(
            "POST",
            f"/api/v1/reservations/{cancelled['id']}/cancel",
            {"expectedRevision": cancelled["revision"]},
            client=self.employee_client,
        )
        self.admin_active = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                self.room_two["id"],
                start="13:00",
                duration=120,
                caseNumber="ADMIN-001",
                tagId="tag-1",
            ),
        ).get_json()
        self.now = datetime(2026, 8, 11, 8, 0, tzinfo=timezone(timedelta(hours=8)))

    def report(self, query="", client=None):
        suffix = f"?{query}" if query else ""
        return (client or self.client).get(f"/api/v1/reports/overview{suffix}")

    def export(self, query="", client=None):
        suffix = f"?{query}" if query else ""
        return (client or self.client).get(
            f"/api/v1/reports/reservations.csv{suffix}"
        )

    def test_report_routes_require_authentication(self):
        anonymous = self.app.test_client()
        overview = self.report("scope=self", anonymous)
        self.assertEqual(overview.status_code, 401, overview.get_json())
        self.assertEqual(overview.get_json()["error"]["code"], "SESSION_REQUIRED")
        export = self.export("scope=self", anonymous)
        self.assertEqual(export.status_code, 401, export.get_json())
        self.assertEqual(export.get_json()["error"]["code"], "SESSION_REQUIRED")

    def test_report_dates_reject_9999_december_with_422_not_500(self):
        # 月桶/周桶的下一界计算不得越过 date.max；与预约历史的 9999-11 上界同线。
        for date_to in ("9999-12-01", "9999-12-31"):
            with self.subTest(dateTo=date_to):
                for responder in (self.report, self.export):
                    response = responder(
                        f"scope=self&dateFrom=9999-12-01&dateTo={date_to}"
                    )
                    self.assertEqual(response.status_code, 422, response.get_json())
                    self.assertEqual(
                        response.get_json()["error"]["code"], "VALIDATION_ERROR"
                    )

    def test_employee_self_scope_has_only_personal_metrics(self):
        response = self.report("scope=self&dateFrom=2026-08-10&dateTo=2026-08-10", self.employee_client)
        payload = response.get_json()
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["metricVersion"], "personal-service-v1")
        self.assertEqual(payload["resolvedScope"]["kind"], "self")
        self.assertEqual(payload["resolvedScope"]["owner"]["id"], self.employee["id"])
        self.assertEqual(
            payload["summary"],
            {
                "activeCount": 2,
                "endedCount": 2,
                "activeDurationMinutes": 150,
                "cancelledCount": 1,
                "cancellationRate": 0.3333,
            },
        )
        self.assertEqual(payload["exportRowCount"], 3)
        self.assertNotIn("personWorkload", payload)
        self.assertNotIn("roomWorkload", payload)
        self.assertEqual(sum(item["activeCount"] for item in payload["tagDistribution"]), 2)

    def test_employee_cannot_request_overall_person_or_owner_id(self):
        for query in (
            "scope=overall",
            f"scope=person&ownerId={self.admin['id']}",
            f"scope=self&ownerId={self.employee['id']}",
        ):
            response = self.report(query, self.employee_client)
            self.assertEqual(response.status_code, 403, response.get_json())
            self.assertEqual(response.get_json()["error"]["code"], "FORBIDDEN")
            self.assertNotIn(self.admin["name"], response.get_data(as_text=True))

    def test_admin_overall_and_person_scopes_reconcile(self):
        overall = self.report(
            "scope=overall&dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()
        self.assertEqual(overall["metricVersion"], "overall-service-v1")
        self.assertEqual(overall["summary"]["activeCount"], 3)
        self.assertEqual(overall["summary"]["endedCount"], 3)
        self.assertEqual(overall["summary"]["activeDurationMinutes"], 270)
        self.assertEqual(overall["summary"]["cancelledCount"], 1)
        workloads = {item["ownerId"]: item for item in overall["personWorkload"]}
        self.assertEqual(workloads[self.employee["id"]]["activeCount"], 2)
        self.assertEqual(workloads[self.admin["id"]]["activeCount"], 1)

        person = self.report(
            f"scope=person&ownerId={self.employee['id']}&dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()
        self.assertEqual(person["metricVersion"], "personal-service-v1")
        self.assertEqual(person["resolvedScope"]["kind"], "person")
        self.assertEqual(person["summary"]["activeCount"], 2)
        self.assertEqual(person["summary"]["activeDurationMinutes"], 150)

    def test_monthly_trend_is_calendar_aligned_and_zero_filled(self):
        payload = self.report(
            "scope=overall&dateFrom=2026-01-01&dateTo=2026-08-17"
        ).get_json()
        self.assertEqual(len(payload["monthlyTrend"]), 8)
        self.assertEqual(
            payload["monthlyTrend"][:7],
            [
                {
                    "monthStart": f"2026-{month:02d}-01",
                    "activeCount": 0,
                    "activeDurationMinutes": 0,
                }
                for month in range(1, 8)
            ],
        )
        self.assertEqual(
            payload["monthlyTrend"][7],
            {
                "monthStart": "2026-08-01",
                "activeCount": 3,
                "activeDurationMinutes": 270,
            },
        )

    def test_overall_rejects_personal_tag_semantics(self):
        response = self.report("scope=overall&tagId=tag-3")
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(
            response.get_json()["error"]["code"],
            "PERSONAL_TAG_OWNER_REQUIRED",
        )

    def test_filters_include_purpose_and_notes_with_literal_wildcards(self):
        purpose = self.report(
            "scope=self&dateFrom=2026-08-10&dateTo=2026-08-10&query=%40%E4%BA%8B%E9%A1%B9",
            self.employee_client,
        ).get_json()
        self.assertEqual(purpose["summary"]["activeCount"], 1)
        literal_percent = self.report(
            "scope=self&dateFrom=2026-08-10&dateTo=2026-08-10&query=%25",
            self.employee_client,
        ).get_json()
        self.assertEqual(literal_percent["summary"]["activeCount"], 0)

    def test_employee_csv_is_bom_crlf_formula_safe_and_self_only(self):
        response = self.export(
            "scope=self&dateFrom=2026-08-10&dateTo=2026-08-10",
            self.employee_client,
        )
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        raw = response.data
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        text = raw[3:].decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][0], "预约日期")
        active_row = next(row for row in rows[1:] if row[7] == "'-10")
        self.assertEqual(active_row[6], "'=2+2")
        self.assertEqual(active_row[8], "'@事项")
        self.assertEqual(active_row[10], "'+SUM(A1:A2)")
        self.assertNotIn("ADMIN-001", text)
        self.assertEqual(response.headers["X-Report-Field-Version"], "reservation-detail-v1")
        self.assertEqual(response.headers["X-Report-Row-Count"], "3")

    def test_admin_csv_current_scope_and_status_are_enforced(self):
        overall = self.export(
            "scope=overall&dateFrom=2026-08-10&dateTo=2026-08-10&status=active"
        )
        self.assertEqual(overall.status_code, 200)
        self.assertEqual(overall.headers["X-Report-Row-Count"], "3")
        person = self.export(
            f"scope=person&ownerId={self.employee['id']}&dateFrom=2026-08-10&dateTo=2026-08-10&status=cancelled"
        )
        self.assertEqual(person.status_code, 200)
        self.assertEqual(person.headers["X-Report-Row-Count"], "1")
        text = person.data[3:].decode("utf-8")
        self.assertIn("EMP-CANCELLED", text)
        self.assertNotIn("ADMIN-001", text)

    def test_export_audit_contains_scope_not_business_content(self):
        response = self.export(
            "scope=self&dateFrom=2026-08-10&dateTo=2026-08-10&query=EMP",
            self.employee_client,
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            from v2app.db import get_db

            row = get_db().execute(
                """
                SELECT * FROM security_audit_log
                WHERE action = 'report.csv_exported'
                ORDER BY occurred_at DESC LIMIT 1
                """
            ).fetchone()
            details = json.loads(row["details_json"])
        self.assertEqual(row["actor_user_id"], self.employee["id"])
        self.assertEqual(details["scope"], "self")
        self.assertTrue(details["queryApplied"])
        self.assertNotIn("EMP", row["details_json"])
        self.assertNotIn("合成测试备注", row["details_json"])

    def test_export_over_limit_is_rejected_and_safely_audited(self):
        with patch("v2app.services.reporting.CSV_EXPORT_LIMIT", 2):
            response = self.export(
                "scope=self&dateFrom=2026-08-10&dateTo=2026-08-10",
                self.employee_client,
            )
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()["error"]["code"], "EXPORT_TOO_LARGE")
        with self.app.app_context():
            from v2app.db import get_db

            row = get_db().execute(
                """
                SELECT details_json FROM security_audit_log
                WHERE action = 'report.csv_exported'
                ORDER BY occurred_at DESC LIMIT 1
                """
            ).fetchone()
        details = json.loads(row["details_json"])
        self.assertEqual(details["result"], "failed")
        self.assertEqual(details["errorCode"], "EXPORT_TOO_LARGE")
        self.assertIsNone(details["rowCount"])

    def test_report_date_range_is_at_most_366_inclusive_days(self):
        accepted = self.report("scope=overall&dateFrom=2024-01-01&dateTo=2024-12-31")
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        rejected = self.report("scope=overall&dateFrom=2024-01-01&dateTo=2025-01-01")
        self.assertEqual(rejected.status_code, 422, rejected.get_json())


if __name__ == "__main__":
    import unittest

    unittest.main()

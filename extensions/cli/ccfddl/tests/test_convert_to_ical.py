from datetime import date

import pytest
from icalendar import Calendar

from ccfddl.convert_to_ical import (
    convert_to_ical,
    get_conference_date_range,
    parse_conference_date_range,
)


@pytest.mark.parametrize(
    ("date_text", "conference_year", "expected"),
    [
        ("June 3-7, 2026", 2026, (date(2026, 6, 3), date(2026, 6, 7))),
        (
            "June 27 - July 1, 2022",
            2022,
            (date(2022, 6, 27), date(2022, 7, 1)),
        ),
        (
            "29 June - 2 July, 2026",
            2026,
            (date(2026, 6, 29), date(2026, 7, 2)),
        ),
        (
            "January 31 - 4 February, 2026",
            2026,
            (date(2026, 1, 31), date(2026, 2, 4)),
        ),
        (
            "December 30-January 2, 2026",
            2026,
            (date(2025, 12, 30), date(2026, 1, 2)),
        ),
        ("April 8, 2026", 2026, (date(2026, 4, 8), date(2026, 4, 8))),
        (
            "November 30 - December 3",
            2026,
            (date(2026, 11, 30), date(2026, 12, 3)),
        ),
        (
            "August 30 - Septemper 1, 2024",
            2024,
            (date(2024, 8, 30), date(2024, 9, 1)),
        ),
    ],
)
def test_parse_conference_date_range(date_text, conference_year, expected):
    assert parse_conference_date_range(date_text, conference_year) == expected


@pytest.mark.parametrize(
    "date_text",
    ["TBD", "To be announced", "April, 2024", "May 2027 (exact dates TBD)"],
)
def test_parse_conference_date_range_skips_ambiguous_values(date_text):
    assert parse_conference_date_range(date_text, 2027) is None


def test_structured_dates_override_legacy_text():
    conf = {
        "year": 2026,
        "date": "TBD",
        "start_date": "2026-06-03",
        "end_date": "2026-06-07",
    }

    assert get_conference_date_range(conf) == (
        date(2026, 6, 3),
        date(2026, 6, 7),
    )


def test_convert_to_ical_adds_conference_event(tmp_path):
    conference_file = tmp_path / "cvpr.yml"
    conference_file.write_text(
        """
- title: CVPR
  description: Conference on Computer Vision and Pattern Recognition
  sub: AI
  rank:
    ccf: A
    core: A*
    thcpl: A
  dblp: cvpr
  confs:
    - year: 2026
      id: cvpr26
      link: https://cvpr.thecvf.com/
      timeline:
        - abstract_deadline: '2025-11-06 23:59:59'
          deadline: '2025-11-13 23:59:59'
      timezone: UTC-12
      date: June 3-7, 2026
      place: Denver, USA
""",
        encoding="utf-8",
    )
    output_file = tmp_path / "deadlines_zh.ics"

    convert_to_ical(
        [str(conference_file)],
        str(output_file),
        lang="zh",
        SUB_MAPPING={"AI": "人工智能"},
    )

    calendar = Calendar.from_ical(output_file.read_bytes())
    events = list(calendar.walk("VEVENT"))
    summaries = {str(event["SUMMARY"]): event for event in events}

    assert set(summaries) == {
        "CVPR 2026 会议",
        "CVPR 2026 摘要截稿",
        "CVPR 2026 截稿日期",
    }
    conference_event = summaries["CVPR 2026 会议"]
    assert conference_event["DTSTART"].dt == date(2026, 6, 3)
    assert conference_event["DTEND"].dt == date(2026, 6, 8)
    assert str(conference_event["UID"]) == "ai-cvpr26-conference@ccfddl.com"
    assert str(conference_event["LOCATION"]) == "Denver, USA"


def test_convert_to_ical_skips_ambiguous_conference_date(tmp_path):
    conference_file = tmp_path / "example.yml"
    conference_file.write_text(
        """
- title: EXAMPLE
  description: Example Conference
  sub: AI
  rank:
    ccf: N
  dblp: example
  confs:
    - year: 2026
      id: example26
      link: https://example.com/
      timeline:
        - deadline: '2025-12-01 23:59:59'
      timezone: UTC
      date: November, 2026
      place: TBD
""",
        encoding="utf-8",
    )
    output_file = tmp_path / "deadlines_en.ics"

    convert_to_ical([str(conference_file)], str(output_file))

    calendar = Calendar.from_ical(output_file.read_bytes())
    summaries = {str(event["SUMMARY"]) for event in calendar.walk("VEVENT")}
    assert summaries == {"EXAMPLE 2026 Deadline"}

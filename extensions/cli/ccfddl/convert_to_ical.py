import yaml
import re
import uuid
from collections import defaultdict
from itertools import combinations
from datetime import date, datetime, timedelta, timezone
from typing import Any
from icalendar import Calendar, Event, Timezone, TimezoneStandard


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "septemper": 9,  # Common typo in existing conference data.
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_date_fragment(fragment: str) -> tuple[int, int] | None:
    """Parse an English month/day or day/month fragment."""
    fragment = fragment.strip()
    match = re.fullmatch(r"([A-Za-z.]+)\s+(\d{1,2})", fragment)
    if match:
        month_text, day_text = match.groups()
    else:
        match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z.]+)", fragment)
        if not match:
            return None
        day_text, month_text = match.groups()

    month = MONTHS.get(month_text.casefold().rstrip("."))
    if month is None:
        return None
    return month, int(day_text)


def parse_conference_date_range(
    date_text: str, conference_year: int
) -> tuple[date, date] | None:
    """Parse an unambiguous conference date range from legacy free text.

    The repository historically stored conference dates as English display text,
    for example ``June 3-7, 2026`` or ``29 June - 2 July, 2026``. Month-only,
    TBD, and otherwise ambiguous values are deliberately not guessed.

    Returns an inclusive ``(start_date, end_date)`` pair.
    """
    normalized = " ".join(str(date_text).strip().split())
    if not normalized:
        return None

    year_match = re.search(r"(?:,\s*|\s+)(\d{4})\s*$", normalized)
    if year_match:
        event_year = int(year_match.group(1))
        normalized = normalized[: year_match.start()].strip(" ,")
    else:
        event_year = int(conference_year)

    fragments = re.split(r"\s*-\s*", normalized)
    if len(fragments) == 1:
        parsed = _parse_date_fragment(fragments[0])
        if parsed is None:
            return None
        month, day = parsed
        try:
            single_date = date(event_year, month, day)
        except ValueError:
            return None
        return single_date, single_date

    if len(fragments) != 2:
        return None

    start_fragment, end_fragment = fragments
    start_parts = _parse_date_fragment(start_fragment)
    if start_parts is None:
        return None

    end_parts = _parse_date_fragment(end_fragment)
    if end_parts is None:
        day_match = re.fullmatch(r"\d{1,2}", end_fragment.strip())
        if day_match is None:
            return None
        end_parts = (start_parts[0], int(day_match.group()))

    start_month, start_day = start_parts
    end_month, end_day = end_parts
    start_year = event_year - 1 if end_month < start_month else event_year

    try:
        start_date = date(start_year, start_month, start_day)
        end_date = date(event_year, end_month, end_day)
    except ValueError:
        return None

    if end_date < start_date:
        return None
    return start_date, end_date


def get_conference_date_range(conf: dict[str, Any]) -> tuple[date, date] | None:
    """Resolve structured dates first, then fall back to legacy display text."""
    start_date_text = conf.get("start_date")
    end_date_text = conf.get("end_date")

    if start_date_text:
        try:
            start_date = date.fromisoformat(str(start_date_text))
            end_date = (
                date.fromisoformat(str(end_date_text))
                if end_date_text
                else start_date
            )
        except ValueError:
            return None
        if end_date < start_date:
            return None
        return start_date, end_date

    if end_date_text:
        return None

    return parse_conference_date_range(conf.get("date", ""), conf["year"])


# 中英类别映射表
def load_mapping(path: str = "conference/types.yml"):
    with open(path, encoding="utf-8") as f:
        types = yaml.safe_load(f)
    if types is None:
        return {}
    SUB_MAPPING = {}
    for types_data in types:
        SUB_MAPPING[types_data["sub"]] = types_data["name"]
    return SUB_MAPPING


def nth_sunday(year: int, month: int, n: int) -> date:
    """返回该年月的第n个星期日"""
    first = date(year, month, 1)
    # date.weekday(): Monday=0 ... Sunday=6
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def is_us_dst(day: date) -> bool:
    """美国夏令时区间: 3月第2个星期日 至 11月第1个星期日"""
    return nth_sunday(day.year, 3, 2) <= day < nth_sunday(day.year, 11, 1)


def get_timezone(tz_str: str, on_date: date | None = None) -> timezone:
    """将时区字符串转换为datetime.timezone对象

    PT (美国太平洋时间) 会随夏令时变化: 夏令时为 UTC-7, 其余为 UTC-8。
    未提供 on_date 时按标准时间 UTC-8 处理。
    """
    if tz_str == "AoE":
        return timezone(timedelta(hours=-12))
    if tz_str == "UTC":
        return timezone.utc
    if tz_str == "PT":
        if on_date is not None and is_us_dst(on_date):
            return timezone(timedelta(hours=-7))
        return timezone(timedelta(hours=-8))
    match = re.match(r"UTC([+-])(\d{1,2})$", tz_str)
    if not match:
        raise ValueError(f"无效的时区格式: {tz_str}")
    sign, hours = match.groups()
    offset = int(hours) if sign == "+" else -int(hours)
    return timezone(timedelta(hours=offset))


def create_vtimezone(tz: timezone) -> Timezone:
    """创建VTIMEZONE组件"""
    tz_offset = tz.utcoffset(datetime.now())
    offset_hours = tz_offset.total_seconds() // 3600
    tzid = f"UTC{offset_hours:+03.0f}:00"

    vtz = Timezone()
    vtz.add("TZID", tzid)

    std = TimezoneStandard()
    std.add("DTSTART", datetime(1970, 1, 1))
    std.add("TZOFFSETFROM", timedelta(hours=offset_hours))
    std.add("TZOFFSETTO", timedelta(hours=offset_hours))
    std.add("TZNAME", tzid)

    vtz.add_component(std)
    return vtz


def convert_to_ical(
    file_paths: list[str], output_path: str, lang: str = "en", SUB_MAPPING={}
):
    cal = Calendar()
    cal.add("prodid", "-//会议截止日历//ccfddl.com//")
    cal.add("version", "2.0")

    added_tzids = set()
    for file_path in file_paths:
        with open(file_path, "r", encoding="utf-8") as f:
            conferences = yaml.safe_load(f)

        for conf_data in conferences:
            title = conf_data["title"]
            sub = conf_data["sub"]
            sub_chinese = SUB_MAPPING.get(sub, sub)
            rank = conf_data["rank"]
            dblp = conf_data["dblp"]

            for conf in conf_data["confs"]:
                year = conf["year"]
                link = conf["link"]
                timeline = conf["timeline"]
                timezone_str = conf["timezone"]
                place = conf["place"]
                conference_date_text = conf["date"]

                level_parts = [
                    f"CCF {rank['ccf']}" if rank["ccf"] != "N" else None,
                    f"CORE {rank['core']}"
                    if rank.get("core", "N") != "N"
                    else None,
                    f"THCPL {rank['thcpl']}"
                    if rank.get("thcpl", "N") != "N"
                    else None,
                ]
                level_desc = ", ".join(
                    line for line in level_parts if line is not None
                ) or None

                conference_date_range = get_conference_date_range(conf)
                if conference_date_range is not None:
                    conference_start, conference_end = conference_date_range
                    conference_event = Event()
                    conference_event.add(
                        "uid", f"{sub.lower()}-{conf['id']}-conference@ccfddl.com"
                    )
                    conference_event.add("dtstamp", datetime.now(timezone.utc))
                    conference_event.add("dtstart", conference_start)
                    # DTEND is exclusive for all-day iCalendar events.
                    conference_event.add(
                        "dtend", conference_end + timedelta(days=1)
                    )

                    if lang == "en":
                        conference_summary = f"{title} {year} Conference"
                        conference_description = [
                            f"{conf_data['description']}",
                            f"🗓️ Date: {conference_date_text}",
                            f"📍 Location: {place}",
                            f"Category: {sub_chinese} ({sub})",
                            level_desc,
                            f"Conference Website: {link}",
                            f"DBLP Index: https://dblp.org/db/conf/{dblp}",
                        ]
                    else:
                        conference_summary = f"{title} {year} 会议"
                        conference_description = [
                            f"{conf_data['description']}",
                            f"🗓️ 会议时间: {conference_date_text}",
                            f"📍 会议地点: {place}",
                            f"分类: {sub_chinese} ({sub})",
                            level_desc,
                            f"会议官网: {link}",
                            f"DBLP索引: https://dblp.org/db/conf/{dblp}",
                        ]

                    conference_event.add("summary", conference_summary)
                    conference_event.add(
                        "description",
                        "\n".join(
                            line
                            for line in conference_description
                            if line is not None
                        ),
                    )
                    conference_event.add("location", place)
                    conference_event.add("url", link)
                    cal.add_component(conference_event)

                for entry in timeline:
                    try:
                        get_timezone(timezone_str)
                    except ValueError:
                        continue

                    # 收集所有需要处理的截止日期
                    deadlines_to_process = []

                    if "abstract_deadline" in entry:
                        deadlines_to_process.append(
                            (
                                ("摘要截稿", "Abstract Deadline"),
                                entry["abstract_deadline"],
                            )
                        )

                    if "deadline" in entry:
                        deadlines_to_process.append(
                            (("截稿日期", "Deadline"), entry["deadline"])
                        )

                    # 如果没有任何截止日期，跳过
                    if not deadlines_to_process:
                        continue

                    # 处理每个截止日期
                    for deadline_type, deadline_str in deadlines_to_process:
                        if deadline_str == "TBD":
                            continue  # 忽略待定日期

                        # 解析日期和时间
                        is_all_day = False
                        try:
                            deadline_dt = datetime.strptime(
                                deadline_str, "%Y-%m-%d %H:%M:%S"
                            )
                        except ValueError:
                            try:
                                deadline_dt = datetime.strptime(
                                    deadline_str, "%Y-%m-%d"
                                )
                                is_all_day = True
                            except ValueError:
                                continue  # 无效日期格式

                        # 按截止日期解析时区 (PT 需要按日期判断夏令时)
                        tz = get_timezone(timezone_str, deadline_dt.date())

                        # 添加VTIMEZONE组件
                        tz_offset = tz.utcoffset(datetime.now())
                        offset_hours = tz_offset.total_seconds() // 3600
                        tzid = f"UTC{offset_hours:+03.0f}:00"

                        if tzid not in added_tzids:
                            vtz = create_vtimezone(tz)
                            cal.add_component(vtz)
                            added_tzids.add(tzid)

                        # 创建事件对象
                        event = Event()
                        event.add("uid", uuid.uuid4())
                        event.add("dtstamp", datetime.now(tz))

                        # 处理时间字段
                        if is_all_day:
                            event.add("dtstart", deadline_dt.date())
                            event.add("dtend", (deadline_dt + timedelta(days=1)).date())
                        else:
                            aware_dt = deadline_dt.replace(tzinfo=tz)
                            event.add("dtstart", aware_dt)
                            event.add("dtend", aware_dt + timedelta(minutes=1))

                        # 构建中英双语摘要
                        if lang == "en":
                            summary = f"{title} {year} {deadline_type[1]}"
                        else:
                            summary = f"{title} {year} {deadline_type[0]}"

                        # 添加注释信息
                        if "comment" in entry:
                            summary += f" [{entry['comment']}]"
                        event.add("summary", summary)

                        # 构建详细描述
                        if lang == "en":
                            description = [
                                f"{conf_data['description']}",
                                f"🗓️ Date: {conference_date_text}",
                                f"📍 Location: {place}",
                                f"⏰ Original Deadline ({timezone_str}): {deadline_str}",
                                f"Category: {sub_chinese} ({sub})",
                                level_desc,
                                f"Conference Website: {link}",
                                f"DBLP Index: https://dblp.org/db/conf/{dblp}",
                            ]
                        else:
                            description = [
                                f"{conf_data['description']}",
                                f"🗓️ 会议时间: {conference_date_text}",
                                f"📍 会议地点: {place}",
                                f"⏰ 原始截止时间 ({timezone_str}): {deadline_str}",
                                f"分类: {sub_chinese} ({sub})",
                                level_desc,
                                f"会议官网: {link}",
                                f"DBLP索引: https://dblp.org/db/conf/{dblp}",
                            ]
                        event.add(
                            "description",
                            "\n".join(
                                line for line in description if line is not None
                            ),
                        )

                        # 添加其他元信息
                        event.add("location", place)
                        event.add("url", link)

                        cal.add_component(event)

    # 写入输出文件
    with open(output_path, "wb") as f:
        f.write(cal.to_ical())


def add_index_entry(index, key: str, file_path: str):
    index[key].add(file_path)


def reverse_index(file_paths: list[str], subs: list[str]):
    index = defaultdict(set)

    for file_path in file_paths:
        with open(file_path, "r", encoding="utf-8") as f:
            conferences = yaml.safe_load(f)

        if conferences is None:
            continue

        for conf_data in conferences:
            sub = conf_data["sub"]
            rank = conf_data["rank"]
            ccf_rank = rank.get("ccf", "N")
            core_rank = rank.get("core", "N")
            thcpl_rank = rank.get("thcpl", "N")
            rank_keys = [
                f"ccf_{ccf_rank}",
                f"core_{core_rank}",
                f"thcpl_{thcpl_rank}",
            ]

            add_index_entry(index, sub, file_path)

            for size in range(1, len(rank_keys) + 1):
                for combo in combinations(rank_keys, size):
                    key = "_".join(combo)
                    add_index_entry(index, key, file_path)
                    add_index_entry(index, f"{key}_{sub}", file_path)

    return {key: sorted(paths) for key, paths in index.items()}


if __name__ == "__main__":
    from xlin import ls, element_mapping

    SUB_MAPPING = load_mapping("conference/types.yml")
    paths = ls("conference", filter=lambda f: f.name != "types.yml")
    index = reverse_index(paths, list(SUB_MAPPING.keys()))
    for lang in ["zh", "en"]:
        convert_to_ical(paths, f"deadlines_{lang}.ics", lang, SUB_MAPPING)
        f = lambda key: (
            len(index[key]) > 0,
            convert_to_ical(
                index[key],
                f"deadlines_{lang}_{key.replace('*', 'star')}.ics",
                lang,
                SUB_MAPPING,
            ),
        )
        element_mapping(index.keys(), f, thread_pool_size=8)
    print("转换完成")

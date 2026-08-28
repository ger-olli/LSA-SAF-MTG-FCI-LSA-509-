import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)
    events_path = Path(args.events)
    output_path = Path(args.output)

    retained = []
    invalid_lines = 0
    missing_timestamp = 0

    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue

            detected = parse_utc(event.get("detected_at_utc"))
            if detected is None:
                detected = parse_utc(event.get("detected_by_workflow_at_utc"))
            if detected is None:
                missing_timestamp += 1
                continue

            if detected >= cutoff:
                retained.append(event)

    payload = {
        "generated_at_utc": now.isoformat(),
        "window_hours": args.hours,
        "source_events_file": args.events,
        "event_count": len(retained),
        "events": retained,
        "integrity": {
            "invalid_json_lines_skipped": invalid_lines,
            "events_without_valid_detection_time_skipped": missing_timestamp,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

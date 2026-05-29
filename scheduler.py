"""
Automatic Video Scheduler for MoneyPrinterTurbo
================================================
Schedules daily video generation + auto-upload to TikTok/Instagram.

Usage:
    python scheduler.py                  # Start scheduler (reads topics.txt)
    python scheduler.py --run-now        # Generate + upload immediately (no wait)
    python scheduler.py --topics-file my_topics.txt

Setup:
    1. Add topics to topics.txt (one per line)
    2. Set upload_post_enabled = true in config.toml
    3. Set upload_post_api_key and upload_post_username in config.toml
    4. Set SCHEDULE_TIMES in this file (e.g. ["09:00", "18:00"])
    5. Run: python scheduler.py
"""

import argparse
import sys
import uuid
import time
import random
from datetime import datetime
from pathlib import Path

from loguru import logger

# ─────────────────────────────────────────
# CONFIGURATION — Edit these values
# ─────────────────────────────────────────

# Times to generate + upload video each day (24h format, server local time)
SCHEDULE_TIMES = ["09:00", "18:00"]

# Path to topics file (one topic per line)
TOPICS_FILE = "topics.txt"

# Default video settings (override per topic using topics.json — see below)
DEFAULT_VOICE     = "en-US-JennyNeural-Female"   # TTS voice
DEFAULT_LANGUAGE  = "English"                     # Script language
DEFAULT_ASPECT    = "portrait"                    # portrait (9:16) or landscape (16:9)
DEFAULT_SOURCE    = "pexels"                      # pexels or pixabay
DEFAULT_HASHTAGS  = "#shorts #viral #trending"   # Appended to TikTok caption

# Retry settings
MAX_RETRIES = 2          # How many times to retry a failed video
RETRY_DELAY = 60         # Seconds to wait between retries

# ─────────────────────────────────────────


def load_topics(topics_file: str) -> list[str]:
    """Load topics from a text file, one topic per line."""
    p = Path(topics_file)
    if not p.exists():
        logger.warning(f"Topics file not found: {topics_file}. Creating sample file.")
        sample_topics = [
            "The history of Azerbaijan",
            "5 habits of highly successful people",
            "How to learn a new language fast",
            "Amazing facts about the human brain",
            "The future of artificial intelligence",
            "How to stay productive when working from home",
            "Top 5 travel destinations in 2026",
            "The science of getting better sleep",
        ]
        p.write_text("\n".join(sample_topics), encoding="utf-8")
        logger.info(f"Created sample topics file: {topics_file}")

    topics = [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    logger.info(f"Loaded {len(topics)} topics from {topics_file}")
    return topics


def pick_topic(topics: list[str]) -> str:
    """Pick a random topic from the list."""
    if not topics:
        raise ValueError("Topics list is empty. Add topics to topics.txt")
    topic = random.choice(topics)
    logger.info(f"Selected topic: '{topic}'")
    return topic


def generate_and_upload(topic: str, attempt: int = 1) -> bool:
    """
    Generate a video for the given topic and upload it.
    Returns True on success, False on failure.
    """
    from app.models.schema import VideoParams, VideoAspect, VideoConcatMode
    from app.services import task as task_service
    from app.services.upload_post import upload_post_service

    task_id = str(uuid.uuid4()).replace("-", "")[:16]
    logger.info(f"{'─'*50}")
    logger.info(f"[Attempt {attempt}] Task ID : {task_id}")
    logger.info(f"[Attempt {attempt}] Topic   : {topic}")
    logger.info(f"[Attempt {attempt}] Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'─'*50}")

    # Build video parameters
    params = VideoParams(
        video_subject=topic,
        voice_name=DEFAULT_VOICE,
        video_language=DEFAULT_LANGUAGE,
        video_aspect=VideoAspect.portrait if DEFAULT_ASPECT == "portrait" else VideoAspect.landscape,
        video_source=DEFAULT_SOURCE,
        video_concat_mode=VideoConcatMode.random,
        video_count=1,
        paragraph_number=1,
        subtitle_enabled=True,
    )

    try:
        result = task_service.start(task_id=task_id, params=params, stop_at="video")
    except Exception as exc:
        logger.error(f"Video generation raised an exception: {exc}")
        return False

    if not result or not result.get("videos"):
        logger.error("Video generation failed — no output file produced.")
        return False

    video_path = result["videos"][0]
    logger.success(f"Video generated: {video_path}")

    # Upload
    if upload_post_service.is_configured():
        caption = f"{topic}\n\n{DEFAULT_HASHTAGS}"
        upload_result = upload_post_service.upload_video(
            video_path=video_path,
            title=caption,
        )
        if upload_result.get("success"):
            req_id = upload_result.get("request_id", "n/a")
            logger.success(f"Uploaded to {upload_post_service.platforms} | request_id={req_id}")
        else:
            logger.warning(f"Upload failed: {upload_result.get('error', 'unknown error')}")
            logger.warning("Video was generated but NOT uploaded. Check upload-post config.")
    else:
        logger.warning(
            "Upload-Post is not configured. Video saved locally only.\n"
            "Set upload_post_enabled=true and add credentials in config.toml to enable auto-upload."
        )

    return True


def run_job(topics_file: str = TOPICS_FILE) -> None:
    """Single scheduler job: pick topic → generate → upload."""
    topics = load_topics(topics_file)
    topic  = pick_topic(topics)

    for attempt in range(1, MAX_RETRIES + 2):   # +2 = initial + MAX_RETRIES
        success = generate_and_upload(topic, attempt=attempt)
        if success:
            break
        if attempt <= MAX_RETRIES:
            logger.warning(f"Retrying in {RETRY_DELAY}s… (attempt {attempt + 1} of {MAX_RETRIES + 1})")
            time.sleep(RETRY_DELAY)
        else:
            logger.error(f"All {MAX_RETRIES + 1} attempts failed for topic: '{topic}'")


def start_scheduler(topics_file: str = TOPICS_FILE) -> None:
    """Start the blocking scheduler loop."""
    try:
        import schedule
    except ImportError:
        logger.error(
            "'schedule' package not found. Install it:\n"
            "  pip install schedule\n"
            "  # or with uv:\n"
            "  uv add schedule"
        )
        sys.exit(1)

    logger.info(f"Scheduler starting. Scheduled times: {SCHEDULE_TIMES}")
    logger.info(f"Topics file: {topics_file}")

    for t in SCHEDULE_TIMES:
        schedule.every().day.at(t).do(run_job, topics_file=topics_file)
        logger.info(f"  Scheduled daily job at {t}")

    logger.info("Scheduler running. Press Ctrl+C to stop.")
    logger.info(f"Next run: {schedule.next_run()}")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # check every 30 seconds
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")


def main():
    parser = argparse.ArgumentParser(
        description="Auto video scheduler for MoneyPrinterTurbo"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Generate and upload one video immediately (skip scheduler)",
    )
    parser.add_argument(
        "--topics-file",
        default=TOPICS_FILE,
        help=f"Path to topics file (default: {TOPICS_FILE})",
    )
    args = parser.parse_args()

    if args.run_now:
        logger.info("--run-now flag detected. Running one job immediately.")
        run_job(topics_file=args.topics_file)
    else:
        start_scheduler(topics_file=args.topics_file)


if __name__ == "__main__":
    main()

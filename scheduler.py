"""
Automatic Video Scheduler for MoneyPrinterTurbo
================================================
Schedules daily video generation + auto-upload to YouTube (Shorts or regular),
TikTok, and/or Instagram.

Usage:
    python scheduler.py                        # Start scheduler (reads topics.txt)
    python scheduler.py --run-now              # Generate + upload immediately
    python scheduler.py --platform youtube     # Override platform for this run
    python scheduler.py --topics-file my.txt   # Use custom topics file

YouTube Setup (one-time):
    1. Go to https://console.cloud.google.com
    2. Create project -> Enable "YouTube Data API v3"
    3. Create OAuth 2.0 credentials -> Download as client_secrets.json
    4. Place client_secrets.json in project root
    5. First run will open browser for Google login (token saved to yt_token.json)
    6. Set YOUTUBE_ENABLED = True below
    7. Run: python scheduler.py --run-now
"""

import argparse
import sys
import uuid
import time
import random
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

# ──────────────────────────────────────────────────
# ► CONFIGURATION — Edit these values
# ──────────────────────────────────────────────────

# ── Schedule ───────────────────────────────────────────
SCHEDULE_TIMES = ["09:00", "18:00"]   # Daily upload times (24h, server local time)
TOPICS_FILE    = "topics.txt"          # One topic per line

# ── Video settings ───────────────────────────────────────
DEFAULT_VOICE    = "en-US-JennyNeural-Female"
DEFAULT_LANGUAGE = "English"
DEFAULT_SOURCE   = "pexels"              # pexels | pixabay | local

# portrait = 9:16 (YouTube Shorts / TikTok)
# landscape = 16:9 (YouTube regular)
DEFAULT_ASPECT   = "portrait"

# ── YouTube ────────────────────────────────────────────
YOUTUBE_ENABLED        = True           # Set True after completing YouTube Setup above
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"   # OAuth2 credentials file
YOUTUBE_TOKEN_FILE     = "yt_token.json"         # Saved after first login (auto-created)
YOUTUBE_PRIVACY        = "public"                # public | unlisted | private
YOUTUBE_CATEGORY_ID    = "22"                    # 22 = People & Blogs | 28 = Science & Tech
YOUTUBE_HASHTAGS       = "#Shorts #viral #trending #facts"
# If aspect=portrait AND title contains #Shorts -> YouTube treats it as a Short
YOUTUBE_MADE_FOR_KIDS  = False

# ── TikTok / Instagram (upload-post.com) ──────────────────
TIKTOK_ENABLED   = False   # Requires upload_post_enabled=true in config.toml

# ── Retry ──────────────────────────────────────────────
MAX_RETRIES  = 2
RETRY_DELAY  = 60   # seconds

# ──────────────────────────────────────────────────


# ──────────────────────────────────────────────────
# YouTube Upload
# ──────────────────────────────────────────────────

def _get_youtube_service():
    """
    Build and return an authenticated YouTube Data API v3 service.
    On first run, opens a browser for OAuth2 consent.
    Token is saved to YOUTUBE_TOKEN_FILE for subsequent runs.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import googleapiclient.discovery
    except ImportError:
        logger.error(
            "Google API libraries not found. Install them:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib\n"
            "  # or with uv:\n"
            "  uv add google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )
        return None

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None

    # Load saved token
    if Path(YOUTUBE_TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN_FILE, SCOPES)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("YouTube token refreshed.")
            except Exception as e:
                logger.warning(f"Token refresh failed ({e}), re-authenticating…")
                creds = None

        if not creds:
            if not Path(YOUTUBE_CLIENT_SECRETS).exists():
                logger.error(
                    f"client_secrets.json not found at: {YOUTUBE_CLIENT_SECRETS}\n"
                    "Download it from Google Cloud Console:\n"
                    "  https://console.cloud.google.com -> APIs & Services -> Credentials"
                )
                return None
            flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.success("YouTube authentication successful.")

        # Save token for next run
        Path(YOUTUBE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
        logger.info(f"YouTube token saved to {YOUTUBE_TOKEN_FILE}")

    return googleapiclient.discovery.build("youtube", "v3", credentials=creds)


def upload_to_youtube(video_path: str, topic: str, description: str = "") -> bool:
    """
    Upload a video to YouTube via Data API v3.
    Returns True on success.
    """
    if not YOUTUBE_ENABLED:
        logger.info("YouTube upload is disabled (YOUTUBE_ENABLED=False).")
        return False

    try:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
    except ImportError:
        logger.error("google-api-python-client not installed. Run: pip install google-api-python-client")
        return False

    youtube = _get_youtube_service()
    if youtube is None:
        return False

    is_short = DEFAULT_ASPECT == "portrait"
    title = f"{topic} {YOUTUBE_HASHTAGS}" if is_short else topic
    # YouTube title max 100 chars
    title = title[:100]

    if not description:
        description = (
            f"{topic}\n\n"
            f"{'This video is a YouTube Short. ' if is_short else ''}"
            f"\n{YOUTUBE_HASHTAGS}\n\n"
            f"Auto-generated with MoneyPrinterTurbo"
        )

    body = {
        "snippet": {
            "title": title,
            "description": description[:5000],
            "categoryId": YOUTUBE_CATEGORY_ID,
            "tags": [t.lstrip("#") for t in YOUTUBE_HASHTAGS.split() if t.startswith("#")],
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "madeForKids": YOUTUBE_MADE_FOR_KIDS,
            "selfDeclaredMadeForKids": YOUTUBE_MADE_FOR_KIDS,
        },
    }

    logger.info(f"Uploading to YouTube: '{title}'")
    logger.info(f"  Privacy : {YOUTUBE_PRIVACY}")
    logger.info(f"  Short   : {is_short}")
    logger.info(f"  File    : {video_path}")

    try:
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,   # 10 MB chunks
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info(f"  Upload progress: {pct}%")

        video_id = response.get("id", "unknown")
        url = f"https://youtu.be/{video_id}"
        logger.success(f"YouTube upload complete! Video ID: {video_id}")
        logger.success(f"URL: {url}")
        return True

    except Exception as e:
        logger.error(f"YouTube upload error: {e}")
        return False


# ──────────────────────────────────────────────────
# Core helpers
# ──────────────────────────────────────────────────

def load_topics(topics_file: str) -> list[str]:
    """Load topics from a text file, one topic per line."""
    p = Path(topics_file)
    if not p.exists():
        logger.warning(f"Topics file not found: {topics_file}. Creating sample file.")
        sample = [
            "The history of Azerbaijan",
            "5 habits of highly successful people",
            "How to learn a new language fast",
            "Amazing facts about the human brain",
            "The future of artificial intelligence",
            "How to stay productive when working from home",
            "Top 5 travel destinations in 2026",
            "The science of getting better sleep",
        ]
        p.write_text("\n".join(sample), encoding="utf-8")
        logger.info(f"Created sample topics file: {topics_file}")

    topics = [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    logger.info(f"Loaded {len(topics)} topics from {topics_file}")
    return topics


def pick_topic(topics: list[str]) -> str:
    if not topics:
        raise ValueError("Topics list is empty. Add topics to topics.txt")
    topic = random.choice(topics)
    logger.info(f"Selected topic: '{topic}'")
    return topic


def generate_and_upload(topic: str, platform: str = "youtube", attempt: int = 1) -> bool:
    """
    Generate a video for the given topic, then upload to the specified platform.
    platform: "youtube" | "tiktok" | "both"
    Returns True on success.
    """
    from app.models.schema import VideoParams, VideoAspect, VideoConcatMode
    from app.services import task as task_service
    from app.services.upload_post import upload_post_service

    task_id = str(uuid.uuid4()).replace("-", "")[:16]
    logger.info(f"{'─'*55}")
    logger.info(f"[Attempt {attempt}] Task    : {task_id}")
    logger.info(f"[Attempt {attempt}] Topic   : {topic}")
    logger.info(f"[Attempt {attempt}] Platform: {platform}")
    logger.info(f"[Attempt {attempt}] Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'─'*55}")

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

    # ─ Generate video ────────────────────────────────────
    try:
        result = task_service.start(task_id=task_id, params=params, stop_at="video")
    except Exception as exc:
        logger.error(f"Video generation exception: {exc}")
        return False

    if not result or not result.get("videos"):
        logger.error("Video generation failed — no output file produced.")
        return False

    video_path = result["videos"][0]
    logger.success(f"Video generated: {video_path}")

    upload_ok = False

    # ─ YouTube upload ──────────────────────────────────
    if platform in ("youtube", "both") and YOUTUBE_ENABLED:
        yt_ok = upload_to_youtube(video_path=video_path, topic=topic)
        upload_ok = upload_ok or yt_ok

    # ─ TikTok/Instagram upload ─────────────────────────
    if platform in ("tiktok", "both") and TIKTOK_ENABLED:
        if upload_post_service.is_configured():
            caption = f"{topic}\n\n{YOUTUBE_HASHTAGS}"
            res = upload_post_service.upload_video(video_path=video_path, title=caption)
            if res.get("success"):
                logger.success(f"TikTok/Instagram upload OK | request_id={res.get('request_id')}")
                upload_ok = True
            else:
                logger.warning(f"TikTok upload failed: {res.get('error', 'unknown')}")
        else:
            logger.warning("TIKTOK_ENABLED=True but upload-post not configured in config.toml.")

    if not YOUTUBE_ENABLED and not TIKTOK_ENABLED:
        logger.warning("No platform enabled. Video saved locally only.")
        logger.warning(f"  Path: {video_path}")
        return True   # Generation succeeded even if no upload

    return upload_ok


# ──────────────────────────────────────────────────
# Scheduler
# ──────────────────────────────────────────────────

def run_job(topics_file: str = TOPICS_FILE, platform: str = "youtube") -> None:
    topics = load_topics(topics_file)
    topic  = pick_topic(topics)

    for attempt in range(1, MAX_RETRIES + 2):
        success = generate_and_upload(topic, platform=platform, attempt=attempt)
        if success:
            break
        if attempt <= MAX_RETRIES:
            logger.warning(f"Retrying in {RETRY_DELAY}s… (attempt {attempt + 1}/{MAX_RETRIES + 1})")
            time.sleep(RETRY_DELAY)
        else:
            logger.error(f"All attempts failed for topic: '{topic}'")


def start_scheduler(topics_file: str = TOPICS_FILE, platform: str = "youtube") -> None:
    try:
        import schedule
    except ImportError:
        logger.error(
            "'schedule' package not found. Install:\n"
            "  pip install schedule   OR   uv add schedule"
        )
        sys.exit(1)

    logger.info(f"Scheduler starting | platform={platform} | times={SCHEDULE_TIMES}")

    for t in SCHEDULE_TIMES:
        schedule.every().day.at(t).do(run_job, topics_file=topics_file, platform=platform)
        logger.info(f"  Scheduled: {t} daily")

    logger.info("Running. Press Ctrl+C to stop.")
    logger.info(f"Next run at: {schedule.next_run()}")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")


def main():
    parser = argparse.ArgumentParser(description="Auto video scheduler — MoneyPrinterTurbo")
    parser.add_argument("--run-now",      action="store_true", help="Run one job immediately")
    parser.add_argument("--topics-file",  default=TOPICS_FILE, help="Path to topics file")
    parser.add_argument(
        "--platform",
        default="youtube",
        choices=["youtube", "tiktok", "both"],
        help="Upload platform (default: youtube)",
    )
    args = parser.parse_args()

    if args.run_now:
        logger.info(f"--run-now | platform={args.platform}")
        run_job(topics_file=args.topics_file, platform=args.platform)
    else:
        start_scheduler(topics_file=args.topics_file, platform=args.platform)


if __name__ == "__main__":
    main()

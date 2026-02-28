"""
AI Note Assistant - Telegram Bot
텔레그램을 통한 메모 입력 채널

데스크톱 GUI(main.py)와 동일한 GeminiClient/NotionClient 파이프라인을 사용하여
텔레그램 메시지로 메모를 입력하고 Notion에 저장합니다.

사용법:
  1. @BotFather에서 봇 생성 후 토큰 발급
  2. config.json에 telegram_bot_token 설정
  3. python telegram_bot.py
"""

import logging
import sys

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from main import ConfigManager, GeminiClient, NotionClient

# 로깅 설정
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("telegram_bot")

config = ConfigManager()


# ============================================================================
# 유틸리티
# ============================================================================

def escape_md(text: str) -> str:
    """MarkdownV2 특수문자 이스케이프"""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def is_allowed_user(chat_id: int) -> bool:
    """허용된 사용자인지 확인 (빈 목록이면 전체 허용)"""
    allowed = config.get("telegram_allowed_users", [])
    if not allowed:
        return True
    return chat_id in allowed


def format_preview(tasks: list[dict]) -> str:
    """태스크 목록을 텔레그램 미리보기 텍스트로 포맷"""
    lines = [f"📋 분석 결과 ({len(tasks)}개 태스크)\n"]

    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. 📌 {task['task_name']}")

        # 메타 라인
        meta_parts = [f"🏢 {task.get('client', 'General')}"]
        if task.get("priority"):
            meta_parts.append(f"⚡ {task['priority']}")
        if task.get("due_date"):
            meta_parts.append(f"📅 {task['due_date']}")
        lines.append(f"   {'  '.join(meta_parts)}")

        # 액션 (신규/기존 매칭)
        if task.get("_action") == "append" and task.get("related_to"):
            lines.append(f"   📎 → 기존: \"{task['related_to']}\"")
        else:
            lines.append("   📄 새 페이지 생성")

        # 태그
        tags = task.get("tags")
        if tags:
            lines.append(f"   🏷 {' '.join('#' + t for t in tags)}")

        # 컨텍스트 요약
        context_note = task.get("context_note")
        if context_note:
            lines.append(f"   📝 {context_note}")

        # 유저 판단 근거
        reasoning = task.get("reasoning")
        if reasoning:
            lines.append(f"   💭 {reasoning}")

        # 이슈
        issue = task.get("issue")
        if issue:
            lines.append(f"   🚨 {issue}")

        lines.append("")

    # 요약
    new_count = sum(1 for t in tasks if t.get("_action") != "append")
    append_count = sum(1 for t in tasks if t.get("_action") == "append")
    summary_parts = []
    if new_count:
        summary_parts.append(f"신규 {new_count}개")
    if append_count:
        summary_parts.append(f"기존 추가 {append_count}개")
    lines.append(f"— {', '.join(summary_parts)}")

    return "\n".join(lines)


# ============================================================================
# 메모 처리 파이프라인 (GeminiClient + NotionClient 재사용)
# ============================================================================

def process_memo(raw_text: str) -> tuple[list[dict], dict]:
    """
    메모 텍스트를 분석하고 저장 준비 완료된 태스크 목록을 반환.
    Returns: (tasks, available_props)
    """
    gemini = GeminiClient(config.get("gemini_api_key"))
    notion = NotionClient(
        config.get("notion_token"),
        config.get("notion_database_id")
    )

    # Phase 1: 기존 태스크 조회 + DB 스키마 감지
    try:
        existing_tasks = notion.query_database(limit=50)
    except Exception:
        existing_tasks = []

    try:
        available_props = notion.detect_available_properties()
    except Exception:
        available_props = {"tags": False, "related_tasks": False}

    # Phase 2: AI 분석
    if existing_tasks:
        tasks = gemini.parse_memo_with_context(raw_text, existing_tasks)
    else:
        tasks = gemini.parse_memo(raw_text)

    if not tasks:
        return [], available_props

    # related_to 이름 → page_id 매핑
    name_to_page = {t["task_name"]: t["page_id"] for t in existing_tasks}
    for task in tasks:
        related_name = task.get("related_to")
        if related_name and related_name in name_to_page:
            task["_related_page_id"] = name_to_page[related_name]
            task["_action"] = "append"
        else:
            task["related_to"] = None
            task["_action"] = "create"

    return tasks, available_props


def save_to_notion(tasks: list[dict], available_props: dict) -> list[dict]:
    """태스크 목록을 Notion에 저장. 결과 목록 반환."""
    notion = NotionClient(
        config.get("notion_token"),
        config.get("notion_database_id")
    )
    return notion.save_tasks(tasks, available_props)


# ============================================================================
# 텔레그램 핸들러
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 시작 명령어"""
    text = (
        "🤖 AI Note Assistant Bot\n\n"
        "메모를 텍스트로 보내주세요.\n"
        "AI가 분석하여 Notion에 자동 저장합니다.\n\n"
        "사용법:\n"
        "• 메모 텍스트 전송 → 분석 → 확인 후 저장\n"
        "• /status — 연결 상태 확인\n"
        "• /help — 도움말"
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """도움말 명령어"""
    text = (
        "📖 사용 가이드\n\n"
        "1️⃣ 메모를 평소처럼 텍스트로 보내세요\n"
        "   예: \"삼성전자 재고실사 담당자 연락함. "
        "3/5까지 연장 요청. 전년도 동일 이슈 재발 우려\"\n\n"
        "2️⃣ AI가 메모를 분석합니다\n"
        "   • 태스크 추출 + 우선순위 판별\n"
        "   • 기존 태스크 매칭 (후속 메모 감지)\n"
        "   • 태그, 컨텍스트 요약 자동 생성\n\n"
        "3️⃣ 미리보기를 확인하고 저장/취소 선택\n\n"
        "명령어:\n"
        "• /start — 봇 소개\n"
        "• /status — Gemini/Notion 연결 상태\n"
        "• /help — 이 도움말"
    )
    await update.message.reply_text(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """연결 상태 확인 명령어"""
    if not config.is_configured():
        await update.message.reply_text(
            "⚠️ API 키가 설정되지 않았습니다.\nconfig.json을 확인해주세요."
        )
        return

    status_msg = await update.message.reply_text("🔍 연결 상태 확인 중...")

    lines = []

    # Gemini 확인
    try:
        gemini = GeminiClient(config.get("gemini_api_key"))
        if gemini.test_connection():
            lines.append("✅ Gemini API — 연결됨")
        else:
            lines.append("❌ Gemini API — 연결 실패")
    except Exception as e:
        lines.append(f"❌ Gemini API — {e}")

    # Notion 확인
    try:
        notion = NotionClient(
            config.get("notion_token"),
            config.get("notion_database_id")
        )
        if notion.test_connection():
            lines.append("✅ Notion API — 연결됨")
        else:
            lines.append("❌ Notion API — 연결 실패")
    except Exception as e:
        lines.append(f"❌ Notion API — {e}")

    await status_msg.edit_text("\n".join(lines))


async def handle_memo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모 텍스트 수신 → AI 분석 → 미리보기"""
    chat_id = update.effective_chat.id

    if not is_allowed_user(chat_id):
        await update.message.reply_text("⛔ 허가되지 않은 사용자입니다.")
        return

    if not config.is_configured():
        await update.message.reply_text(
            "⚠️ API 키가 설정되지 않았습니다.\nconfig.json을 확인해주세요."
        )
        return

    raw_text = update.message.text.strip()
    if len(raw_text) < 5:
        await update.message.reply_text("📝 좀 더 구체적으로 메모를 작성해주세요.")
        return

    # 분석 중 상태 메시지
    status_msg = await update.message.reply_text("🔍 메모 분석 중...")

    try:
        tasks, available_props = process_memo(raw_text)
    except Exception as e:
        logger.error(f"메모 분석 실패: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ 메모 분석 중 오류가 발생했습니다.\n{e}")
        return

    if not tasks:
        await status_msg.edit_text("⚠️ AI가 태스크를 추출하지 못했습니다.\n메모를 좀 더 구체적으로 작성해보세요.")
        return

    # 유저 데이터에 임시 저장 (콜백에서 사용)
    context.user_data["pending_tasks"] = tasks
    context.user_data["available_props"] = available_props

    # 미리보기 + 인라인 버튼
    preview = format_preview(tasks)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 저장", callback_data="save"),
            InlineKeyboardButton("❌ 취소", callback_data="cancel"),
        ]
    ])

    await status_msg.edit_text(preview, reply_markup=keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인라인 버튼 콜백 (저장/취소)"""
    query = update.callback_query
    await query.answer()

    if query.data == "save":
        tasks = context.user_data.get("pending_tasks")
        available_props = context.user_data.get("available_props")

        if not tasks:
            await query.edit_message_text("⚠️ 저장할 태스크가 없습니다.")
            return

        await query.edit_message_text("💾 Notion에 저장 중...")

        try:
            results = save_to_notion(tasks, available_props)
        except Exception as e:
            logger.error(f"Notion 저장 실패: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Notion 저장 중 오류가 발생했습니다.\n{e}")
            context.user_data.clear()
            return

        created = sum(1 for r in results if r["action"] == "created")
        appended = sum(1 for r in results if r["action"] == "appended")

        msg = "✅ 완료! "
        parts = []
        if created:
            parts.append(f"신규 {created}개")
        if appended:
            parts.append(f"추가 {appended}개")
        msg += ", ".join(parts) + " 태스크가 Notion에 저장되었습니다."

        await query.edit_message_text(msg)
        context.user_data.clear()

    elif query.data == "cancel":
        await query.edit_message_text("❌ 저장이 취소되었습니다.")
        context.user_data.clear()


# ============================================================================
# 엔트리 포인트
# ============================================================================

def main():
    bot_token = config.get("telegram_bot_token")
    if not bot_token:
        print("❌ telegram_bot_token이 config.json에 설정되지 않았습니다.")
        print()
        print("설정 방법:")
        print("  1. 텔레그램에서 @BotFather 검색")
        print("  2. /newbot 명령으로 봇 생성")
        print("  3. 발급된 토큰을 config.json의 telegram_bot_token에 입력")
        sys.exit(1)

    if not config.is_configured():
        print("❌ Gemini/Notion API 키가 config.json에 설정되지 않았습니다.")
        sys.exit(1)

    app = Application.builder().token(bot_token).build()

    # 핸들러 등록
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_memo))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 AI Note Assistant 텔레그램 봇 실행 중...")
    app.run_polling()


if __name__ == "__main__":
    main()

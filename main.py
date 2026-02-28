"""
AI Note Assistant
메모 자동화 데스크톱 애플리케이션

Features:
- 텍스트 메모를 AI로 구조화된 JSON으로 변환
- Notion 데이터베이스에 자동 적재
- Always on Top 지원
"""

import json
import os
import platform
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import (
    Tk, Toplevel, Label, Entry, Button, Frame, Checkbutton, BooleanVar,
    StringVar, Text, Scrollbar, Canvas, Listbox, OptionMenu, messagebox,
    END, WORD, SINGLE, RIGHT, Y, BOTH, LEFT, X, TOP, BOTTOM
)
from tkinter.ttk import Style, Separator

from google import genai
import requests

# ============================================================================
# 설정 관리
# ============================================================================

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "notion_token": "",
    "notion_database_id": "",
    "always_on_top": True
}

# 플랫폼별 폰트 설정
def get_system_font():
    """OS에 따른 적절한 시스템 폰트 반환"""
    system = platform.system()
    if system == "Windows":
        return ("맑은 고딕", 11)
    elif system == "Darwin":  # macOS
        return ("Apple SD Gothic Neo", 12)
    else:  # Linux
        return ("Noto Sans CJK KR", 11)

SYSTEM_FONT = get_system_font()


class ConfigManager:
    """로컬 config.json 파일 관리"""
    
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = config_path
        self.config = self.load()
    
    def load(self) -> dict:
        """설정 파일 로드. 없으면 기본값 반환"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # 기본값과 병합 (새 필드 추가 대응)
                    return {**DEFAULT_CONFIG, **loaded}
            except (json.JSONDecodeError, IOError):
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()
    
    def save(self) -> None:
        """설정을 파일에 저장"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
    
    def get(self, key: str, default=None):
        return self.config.get(key, default)
    
    def set(self, key: str, value) -> None:
        self.config[key] = value
    
    def is_configured(self) -> bool:
        """필수 API 키가 설정되어 있는지 확인"""
        return bool(
            self.config.get("gemini_api_key") and
            self.config.get("notion_token") and
            self.config.get("notion_database_id")
        )


# ============================================================================
# Gemini API 클라이언트
# ============================================================================

class GeminiClient:
    """Google Gemini API 통신"""
    
    SYSTEM_PROMPT_TEMPLATE = """You are a senior audit manager's assistant at an accounting firm.

CONTEXT:
- Current datetime: {current_datetime}
- Today is {weekday_name} ({weekday_korean})
- This week: {week_start} ~ {week_end}

INSTRUCTIONS:
1. Parse the raw memo into structured task(s)
2. Convert relative dates to absolute format (YYYY-MM-DD):
   - "오늘" → {today}
   - "내일" → {tomorrow}
   - "모레" → {day_after_tomorrow}
   - "이번 주" → this week ({week_start} ~ {week_end})
   - "다음 주" → next week ({next_week_start} ~ {next_week_end})
   - "다음 주 초" → {next_week_start}
   - "다음 주 말" → {next_week_end}
   - "월말" → {month_end}
3. Detect issues from negative keywords (지연, 미수령, 연락두절, 연락안됨, 오류, 문제, 이슈, 리스크, 우려)
4. If issues detected, set Priority to "High"
5. Extract company/client name if mentioned, otherwise use "General"
6. Return ONLY a valid JSON array, no markdown code blocks

OUTPUT SCHEMA (return as JSON array):
[
  {{
    "task_name": "string (명사형 종결, 50자 이내)",
    "client": "string (회사명 or 'General')",
    "due_date": "YYYY-MM-DD or null if not specified",
    "priority": "High|Medium|Low",
    "issue": "string describing blocker or null",
    "original_text": "string (원본 메모 발췌)"
  }}
]

IMPORTANT: Return ONLY the JSON array. Do not wrap in markdown code blocks."""

    CONTEXT_PROMPT_TEMPLATE = """You are a senior audit manager's assistant at an accounting firm.

CONTEXT:
- Current datetime: {current_datetime}
- Today is {weekday_name} ({weekday_korean})
- This week: {week_start} ~ {week_end}

EXISTING TASKS IN DATABASE:
{existing_tasks_list}

INSTRUCTIONS:
1. Parse the raw memo into structured task(s)
2. Convert relative dates to absolute format (YYYY-MM-DD):
   - "오늘" → {today}
   - "내일" → {tomorrow}
   - "모레" → {day_after_tomorrow}
   - "이번 주" → this week ({week_start} ~ {week_end})
   - "다음 주" → next week ({next_week_start} ~ {next_week_end})
   - "다음 주 초" → {next_week_start}
   - "다음 주 말" → {next_week_end}
   - "월말" → {month_end}
3. Detect issues from negative keywords (지연, 미수령, 연락두절, 연락안됨, 오류, 문제, 이슈, 리스크, 우려)
4. If issues detected, set Priority to "High"
5. Extract company/client name if mentioned, otherwise use "General"
6. Follow-up detection:
   - Compare the memo against EXISTING TASKS above
   - If the memo is clearly a follow-up (same client AND same topic), set "related_to" to the EXACT task name from the list
   - Only match when there is a clear contextual connection. If unsure, set null
7. Generate 2-5 descriptive tags/keywords for categorization
8. Write a brief context_note summarizing the key update in this memo entry
9. Extract the user's own reasoning/judgment from the memo AS-IS:
   - Look for expressions like "왜냐하면", "이유는", "판단 근거", "~해서", "~때문에", "~하려고"
   - Preserve the user's original wording - do NOT fabricate or paraphrase
   - If no explicit reasoning is present, set to null
10. Return ONLY a valid JSON array, no markdown code blocks

OUTPUT SCHEMA (return as JSON array):
[
  {{{{
    "task_name": "string (명사형 종결, 50자 이내)",
    "client": "string (회사명 or 'General')",
    "due_date": "YYYY-MM-DD or null if not specified",
    "priority": "High|Medium|Low",
    "issue": "string describing blocker or null",
    "original_text": "string (원본 메모 발췌)",
    "tags": ["keyword1", "keyword2"],
    "context_note": "string (이 메모의 핵심 업데이트 요약)",
    "reasoning": "string (유저의 판단 근거 원문) or null",
    "related_to": "exact existing task name or null"
  }}}}
]

IMPORTANT: Return ONLY the JSON array. Do not wrap in markdown code blocks."""

    WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60_000}
        )
        self.model_name = "gemini-2.5-flash"

    def _get_date_context(self) -> dict:
        """현재 날짜/시간 컨텍스트 계산"""
        now = datetime.now()
        today = now.date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        next_week_start = week_end + timedelta(days=1)
        next_week_end = next_week_start + timedelta(days=6)
        if today.month == 12:
            month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return {
            "current_datetime": now.strftime("%Y-%m-%d %H:%M"),
            "weekday_name": self.WEEKDAYS_EN[today.weekday()],
            "weekday_korean": self.WEEKDAYS_KO[today.weekday()],
            "today": today.isoformat(),
            "tomorrow": (today + timedelta(days=1)).isoformat(),
            "day_after_tomorrow": (today + timedelta(days=2)).isoformat(),
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "next_week_start": next_week_start.isoformat(),
            "next_week_end": next_week_end.isoformat(),
            "month_end": month_end.isoformat(),
        }

    def _build_system_prompt(self) -> str:
        """현재 날짜/시간 정보를 포함한 시스템 프롬프트 생성"""
        return self.SYSTEM_PROMPT_TEMPLATE.format(**self._get_date_context())

    @staticmethod
    def _format_existing_tasks(existing_tasks: list[dict]) -> str:
        """기존 태스크 목록을 프롬프트용 문자열로 변환"""
        if not existing_tasks:
            return "(No existing tasks found)"
        lines = []
        for t in existing_tasks:
            line = f"- \"{t['task_name']}\" (Client: {t['client']}, Priority: {t['priority']})"
            if t.get("due_date"):
                line += f" Due: {t['due_date']}"
            lines.append(line)
        return "\n".join(lines)

    def _build_context_prompt(self, existing_tasks: list[dict]) -> str:
        """기존 태스크 컨텍스트를 포함한 시스템 프롬프트 생성"""
        ctx = self._get_date_context()
        ctx["existing_tasks_list"] = self._format_existing_tasks(existing_tasks)
        return self.CONTEXT_PROMPT_TEMPLATE.format(**ctx)

    def _parse_response(self, response_text: str) -> list[dict]:
        """AI 응답 텍스트를 파싱하여 태스크 리스트 반환"""
        result_text = response_text.strip()
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            result_text = "\n".join(lines)
        try:
            tasks = json.loads(result_text)
            if isinstance(tasks, dict):
                tasks = [tasks]
            return tasks
        except json.JSONDecodeError as e:
            raise ValueError(f"AI 응답 파싱 실패: {e}\n응답: {result_text[:200]}")

    def parse_memo(self, raw_text: str) -> list[dict]:
        """메모를 분석하여 구조화된 태스크 리스트 반환"""
        system_prompt = self._build_system_prompt()
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=f"{system_prompt}\n\nParse this memo:\n\n{raw_text}"
        )
        return self._parse_response(response.text)

    def parse_memo_with_context(self, raw_text: str, existing_tasks: list[dict]) -> list[dict]:
        """기존 태스크 맥락을 포함하여 메모 분석"""
        system_prompt = self._build_context_prompt(existing_tasks)
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=f"{system_prompt}\n\nParse this memo:\n\n{raw_text}"
        )
        return self._parse_response(response.text)

    def test_connection(self) -> bool:
        """API 연결 테스트"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents="Say 'OK' if you can read this."
            )
            return "OK" in response.text.upper()
        except Exception:
            return False


# ============================================================================
# Notion API 클라이언트
# ============================================================================

class NotionClient:
    """Notion API 통신"""

    API_VERSION = "2022-06-28"
    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self.API_VERSION,
            "Content-Type": "application/json"
        }

    # ------------------------------------------------------------------
    # 속성 추출 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(prop: dict) -> str:
        try:
            return prop["title"][0]["plain_text"]
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _extract_select(prop: dict) -> str:
        try:
            return prop["select"]["name"]
        except (KeyError, TypeError):
            return ""

    @staticmethod
    def _extract_date(prop: dict) -> str:
        try:
            return prop["date"]["start"]
        except (KeyError, TypeError):
            return ""

    # ------------------------------------------------------------------
    # 콘텐츠 블록 빌더
    # ------------------------------------------------------------------

    @staticmethod
    def _build_divider_block() -> dict:
        return {"object": "block", "type": "divider", "divider": {}}

    @staticmethod
    def _build_heading3_block(text: str) -> dict:
        return {
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}
        }

    @staticmethod
    def _build_paragraph_block(text: str) -> dict:
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
        }

    @staticmethod
    def _build_callout_block(text: str, emoji: str = "📋") -> dict:
        return {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}],
                "icon": {"type": "emoji", "emoji": emoji}
            }
        }

    # ------------------------------------------------------------------
    # 페이지 본문 블록 조립
    # ------------------------------------------------------------------

    def build_new_page_body_blocks(self, task: dict) -> list[dict]:
        """새 페이지의 본문 블록 생성 (메타데이터 포함)"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tags_str = ", ".join(task.get("tags", [])) if task.get("tags") else "없음"
        meta_text = (
            f"생성일: {now}\n"
            f"태그: {tags_str}\n"
            f"고객사: {task.get('client', 'General')}"
        )
        blocks = [
            self._build_callout_block(meta_text, "📋"),
            self._build_divider_block(),
            self._build_heading3_block(f"📝 {now}"),
        ]
        content = task.get("context_note") or task.get("original_text", "")
        if content:
            blocks.append(self._build_paragraph_block(content))
        if task.get("reasoning"):
            blocks.append(self._build_callout_block(task["reasoning"], "💭"))
        if task.get("issue"):
            blocks.append(self._build_callout_block(task["issue"], "🚨"))
        return blocks

    def build_append_blocks(self, task: dict) -> list[dict]:
        """기존 페이지에 추가할 블록 생성"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        blocks = [
            self._build_divider_block(),
            self._build_heading3_block(f"📝 {now} (추가 메모)"),
        ]
        content = task.get("context_note") or task.get("original_text", "")
        if content:
            blocks.append(self._build_paragraph_block(content))
        if task.get("reasoning"):
            blocks.append(self._build_callout_block(task["reasoning"], "💭"))
        if task.get("issue"):
            blocks.append(self._build_callout_block(task["issue"], "🚨"))
        return blocks

    # ------------------------------------------------------------------
    # 페이지 속성 빌더
    # ------------------------------------------------------------------

    def _build_page_properties(self, task: dict, available_props: dict = None) -> dict:
        """태스크 딕셔너리를 Notion 페이지 속성으로 변환"""
        if available_props is None:
            available_props = {}
        properties = {
            "Task Name": {
                "title": [{"text": {"content": task.get("task_name", "Untitled")[:100]}}]
            },
            "Client": {
                "select": {"name": task.get("client", "General")}
            },
            "Priority": {
                "select": {"name": task.get("priority", "Medium")}
            },
            "Original Text": {
                "rich_text": [{"text": {"content": task.get("original_text", "")[:2000]}}]
            }
        }
        if task.get("due_date"):
            properties["Due Date"] = {"date": {"start": task["due_date"]}}
        if task.get("issue"):
            properties["Issue/Blocker"] = {
                "rich_text": [{"text": {"content": task["issue"][:2000]}}]
            }
        # 선택적 속성 (DB에 존재할 때만)
        if available_props.get("tags") and task.get("tags"):
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in task["tags"][:10]]
            }
        if available_props.get("related_tasks") and task.get("_related_page_id"):
            properties["Related Tasks"] = {
                "relation": [{"id": task["_related_page_id"]}]
            }
        return properties

    def _build_update_properties(self, task: dict) -> dict:
        """업데이트할 속성만 추출"""
        props = {}
        if task.get("priority"):
            props["Priority"] = {"select": {"name": task["priority"]}}
        if task.get("issue"):
            props["Issue/Blocker"] = {
                "rich_text": [{"text": {"content": task["issue"][:2000]}}]
            }
        if task.get("due_date"):
            props["Due Date"] = {"date": {"start": task["due_date"]}}
        return props

    # ------------------------------------------------------------------
    # API 호출
    # ------------------------------------------------------------------

    def query_database(self, limit: int = 50) -> list[dict]:
        """최근 태스크 목록 조회"""
        payload = {
            "page_size": limit,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}]
        }
        response = requests.post(
            f"{self.BASE_URL}/databases/{self.database_id}/query",
            headers=self.headers,
            json=payload,
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Notion 조회 오류: {response.status_code}")
        results = response.json().get("results", [])
        tasks = []
        for page in results:
            props = page.get("properties", {})
            tasks.append({
                "page_id": page["id"],
                "task_name": self._extract_title(props.get("Task Name", {})),
                "client": self._extract_select(props.get("Client", {})),
                "priority": self._extract_select(props.get("Priority", {})),
                "due_date": self._extract_date(props.get("Due Date", {})),
                "created_time": page.get("created_time", ""),
            })
        return tasks

    def detect_available_properties(self) -> dict:
        """데이터베이스 스키마에서 선택적 속성 감지"""
        response = requests.get(
            f"{self.BASE_URL}/databases/{self.database_id}",
            headers=self.headers,
            timeout=15
        )
        if response.status_code != 200:
            return {"tags": False, "related_tasks": False}
        db_props = response.json().get("properties", {})
        return {
            "tags": "Tags" in db_props and db_props["Tags"].get("type") == "multi_select",
            "related_tasks": "Related Tasks" in db_props and db_props["Related Tasks"].get("type") == "relation",
        }

    def create_page(self, task: dict) -> dict:
        """Notion 데이터베이스에 새 페이지 생성"""
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_page_properties(task)
        }
        response = requests.post(
            f"{self.BASE_URL}/pages",
            headers=self.headers,
            json=payload,
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Notion API 오류: {response.status_code} - {response.text}")
        return response.json()

    def create_page_with_body(self, task: dict, available_props: dict = None) -> dict:
        """본문 블록 포함하여 Notion 페이지 생성"""
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_page_properties(task, available_props),
            "children": self.build_new_page_body_blocks(task)
        }
        response = requests.post(
            f"{self.BASE_URL}/pages",
            headers=self.headers,
            json=payload,
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Notion API 오류: {response.status_code} - {response.text}")
        return response.json()

    def append_blocks(self, page_id: str, blocks: list[dict]) -> dict:
        """기존 페이지에 콘텐츠 블록 추가"""
        response = requests.patch(
            f"{self.BASE_URL}/blocks/{page_id}/children",
            headers=self.headers,
            json={"children": blocks},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Notion 블록 추가 오류: {response.status_code} - {response.text}")
        return response.json()

    def update_page_properties(self, page_id: str, properties: dict) -> dict:
        """기존 페이지 속성 업데이트"""
        response = requests.patch(
            f"{self.BASE_URL}/pages/{page_id}",
            headers=self.headers,
            json={"properties": properties},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"Notion 업데이트 오류: {response.status_code} - {response.text}")
        return response.json()

    def create_pages(self, tasks: list[dict]) -> list[dict]:
        """여러 태스크를 Notion에 일괄 생성 (하위 호환)"""
        results = []
        for task in tasks:
            result = self.create_page(task)
            results.append(result)
        return results

    def save_tasks(self, tasks: list[dict], available_props: dict = None) -> list[dict]:
        """태스크 목록을 Notion에 저장 (신규 생성 또는 기존 페이지에 추가)"""
        results = []
        for task in tasks:
            related_page_id = task.get("_related_page_id")
            if related_page_id:
                blocks = self.build_append_blocks(task)
                self.append_blocks(related_page_id, blocks)
                update_props = self._build_update_properties(task)
                if update_props:
                    self.update_page_properties(related_page_id, update_props)
                results.append({"id": related_page_id, "action": "appended"})
            else:
                result = self.create_page_with_body(task, available_props)
                results.append({"id": result["id"], "action": "created"})
        return results

    def test_connection(self) -> bool:
        """API 연결 및 데이터베이스 접근 테스트"""
        try:
            response = requests.get(
                f"{self.BASE_URL}/databases/{self.database_id}",
                headers=self.headers,
                timeout=15
            )
            return response.status_code == 200
        except Exception:
            return False


# ============================================================================
# 설정 다이얼로그
# ============================================================================

class SettingsDialog:
    """설정 팝업 윈도우"""
    
    def __init__(self, parent, config_manager: ConfigManager, on_save_callback=None):
        self.config = config_manager
        self.on_save_callback = on_save_callback
        self.parent = parent
        
        self.dialog = Toplevel(parent)
        self.dialog.title("⚙️ 설정")
        width, height = 550, 400
        self.dialog.geometry(f"{width}x{height}")
        self.dialog.resizable(True, True)
        self.dialog.minsize(450, 350)

        # 설정창을 항상 최상위로 표시 (팝업처럼 동작)
        self.dialog.attributes("-topmost", True)

        # 모달 다이얼로그 설정
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 중앙 정렬
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
        
        # 창을 앞으로 가져오고 포커스 (지연 후 다시 호출로 확실히 포커스)
        self.dialog.lift()
        self.dialog.focus_force()
        self.dialog.after(10, lambda: self.dialog.focus_force())
        
        self._create_widgets()
    
    @staticmethod
    def _make_toggle_entry(parent, text_var, show_char="*"):
        """비밀번호 표시/숨기기 토글이 있는 Entry 생성"""
        frame = Frame(parent)
        entry = Entry(frame, textvariable=text_var, show=show_char, width=50)
        entry.pack(side=LEFT, fill=X, expand=True)
        def toggle():
            if entry.cget("show") == "*":
                entry.config(show="")
                btn.config(text="🙈")
            else:
                entry.config(show="*")
                btn.config(text="👁️")
        btn = Button(frame, text="👁️", command=toggle, width=3)
        btn.pack(side=RIGHT, padx=(5, 0))
        return frame

    def _create_widgets(self):
        main_frame = Frame(self.dialog, padx=20, pady=20)
        main_frame.pack(fill=BOTH, expand=True)

        # Gemini API Key
        Label(main_frame, text="Gemini API Key:", anchor="w").pack(fill=X, pady=(0, 5))
        self.gemini_key_var = StringVar(value=self.config.get("gemini_api_key", ""))
        self._make_toggle_entry(main_frame, self.gemini_key_var).pack(fill=X, pady=(0, 15))

        # Notion Token
        Label(main_frame, text="Notion Integration Token:", anchor="w").pack(fill=X, pady=(0, 5))
        self.notion_token_var = StringVar(value=self.config.get("notion_token", ""))
        self._make_toggle_entry(main_frame, self.notion_token_var).pack(fill=X, pady=(0, 15))

        # Notion Database ID
        Label(main_frame, text="Notion Database ID:", anchor="w").pack(fill=X, pady=(0, 5))
        self.notion_db_var = StringVar(value=self.config.get("notion_database_id", ""))
        Entry(main_frame, textvariable=self.notion_db_var, width=50).pack(fill=X, pady=(0, 20))

        # 버튼 프레임
        btn_frame = Frame(main_frame)
        btn_frame.pack(fill=X)

        self._test_btn = Button(btn_frame, text="🔗 연결 테스트", command=self._test_connections, width=15)
        self._test_btn.pack(side=LEFT)
        Button(btn_frame, text="취소", command=self.dialog.destroy, width=10).pack(side=RIGHT, padx=(10, 0))
        Button(btn_frame, text="💾 저장", command=self._save, width=10).pack(side=RIGHT)
    
    def _test_connections(self):
        """API 연결 테스트 (비동기)"""
        self._test_btn.config(state="disabled", text="🔄 테스트 중...")
        gemini_key = self.gemini_key_var.get().strip()
        notion_token = self.notion_token_var.get().strip()
        notion_db = self.notion_db_var.get().strip()

        thread = threading.Thread(
            target=self._test_connections_async,
            args=(gemini_key, notion_token, notion_db)
        )
        thread.daemon = True
        thread.start()

    def _test_connections_async(self, gemini_key, notion_token, notion_db):
        """백그라운드에서 API 연결 테스트 실행"""
        results = []

        # Gemini 테스트
        if gemini_key:
            try:
                client = GeminiClient(gemini_key)
                if client.test_connection():
                    results.append("✅ Gemini API: 연결 성공")
                else:
                    results.append("❌ Gemini API: 연결 실패")
            except Exception as e:
                results.append(f"❌ Gemini API: {str(e)[:80]}")
        else:
            results.append("⚠️ Gemini API Key가 입력되지 않았습니다")

        # Notion 테스트
        if notion_token and notion_db:
            try:
                client = NotionClient(notion_token, notion_db)
                if client.test_connection():
                    results.append("✅ Notion API: 연결 성공")
                else:
                    results.append("❌ Notion API: 연결 실패 (토큰 또는 DB ID 확인)")
            except Exception as e:
                results.append(f"❌ Notion API: {str(e)[:80]}")
        else:
            results.append("⚠️ Notion Token 또는 Database ID가 입력되지 않았습니다")

        try:
            self.dialog.after(0, lambda: self._show_test_results(results))
        except Exception:
            pass  # 다이얼로그가 이미 닫힌 경우

    def _show_test_results(self, results):
        """연결 테스트 결과 표시"""
        try:
            self._test_btn.config(state="normal", text="🔗 연결 테스트")
        except Exception:
            pass
        messagebox.showinfo("연결 테스트 결과", "\n".join(results))
    
    def _save(self):
        """설정 저장"""
        self.config.set("gemini_api_key", self.gemini_key_var.get().strip())
        self.config.set("notion_token", self.notion_token_var.get().strip())
        self.config.set("notion_database_id", self.notion_db_var.get().strip())
        self.config.save()
        
        if self.on_save_callback:
            self.on_save_callback()
        
        messagebox.showinfo("저장 완료", "설정이 저장되었습니다.")
        self.dialog.destroy()


# ============================================================================
# 메인 애플리케이션
# ============================================================================

class NoteAssistantApp:
    """AI Note Assistant 메인 앱"""
    
    def __init__(self):
        self.config = ConfigManager()
        self._memo_history: list[dict] = []
        self._history_max = 20

        # 메인 윈도우 설정
        self.root = Tk()
        self.root.title("📝 AI Note Assistant")
        self.root.geometry("400x500")
        self.root.minsize(350, 400)
        
        # Always on Top 설정
        self.always_on_top_var = BooleanVar(value=self.config.get("always_on_top", True))
        self._update_topmost()
        
        self._create_widgets()
        self._bind_shortcuts()
        
        # 최초 실행 시 설정 확인
        if not self.config.is_configured():
            self.root.after(100, self._show_settings)
    
    def _create_widgets(self):
        """UI 위젯 생성"""
        # 상단 툴바
        toolbar = Frame(self.root, padx=10, pady=5)
        toolbar.pack(fill=X)
        
        Button(toolbar, text="⚙️ 설정", command=self._show_settings).pack(side=LEFT)
        Button(toolbar, text="📋 기록", command=self._show_history).pack(side=LEFT, padx=(5, 0))

        Checkbutton(
            toolbar,
            text="📌 항상 위",
            variable=self.always_on_top_var,
            command=self._toggle_topmost
        ).pack(side=RIGHT)
        
        # 구분선
        Separator(self.root, orient="horizontal").pack(fill=X, padx=10)
        
        # 힌트 레이블
        hint_frame = Frame(self.root, padx=10, pady=5)
        hint_frame.pack(fill=X)
        Label(
            hint_frame,
            text="💡 업무 메모를 입력하고 Ctrl+Enter로 저장하세요",
            fg="gray",
            font=("", 9)
        ).pack(anchor="w")
        
        # 텍스트 입력 영역
        text_frame = Frame(self.root, padx=10, pady=5)
        text_frame.pack(fill=BOTH, expand=True)
        
        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side=RIGHT, fill=Y)
        
        self.text_input = Text(
            text_frame,
            wrap=WORD,
            font=SYSTEM_FONT,
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=10
        )
        self.text_input.pack(fill=BOTH, expand=True)
        scrollbar.config(command=self.text_input.yview)
        
        # 버튼 영역
        btn_frame = Frame(self.root, padx=10, pady=10)
        btn_frame.pack(fill=X)
        
        self.submit_btn = Button(
            btn_frame,
            text="📤 저장 (Ctrl+Enter)",
            command=self._on_submit,
            font=("", 10),
            height=2
        )
        self.submit_btn.pack(fill=X)
        
        # 상태바
        self.status_var = StringVar(value="대기 중")
        status_bar = Frame(self.root, padx=10, pady=5, relief="sunken", bd=1)
        status_bar.pack(fill=X, side=BOTTOM)
        Label(status_bar, textvariable=self.status_var, anchor="w").pack(fill=X)
    
    def _bind_shortcuts(self):
        """키보드 단축키 바인딩"""
        self.root.bind("<Control-Return>", lambda e: self._on_submit())
        self.root.bind("<Command-Return>", lambda e: self._on_submit())  # macOS
    
    def _update_topmost(self):
        """창 최상위 설정 업데이트"""
        self.root.attributes("-topmost", self.always_on_top_var.get())
    
    def _toggle_topmost(self):
        """Always on Top 토글"""
        self._update_topmost()
        self.config.set("always_on_top", self.always_on_top_var.get())
        self.config.save()
    
    def _show_settings(self):
        """설정 다이얼로그 표시"""
        SettingsDialog(self.root, self.config, self._on_settings_saved)
    
    def _on_settings_saved(self):
        """설정 저장 후 콜백"""
        pass  # 필요시 클라이언트 재초기화

    def _show_history(self):
        """메모 이전 기록 다이얼로그"""
        if not self._memo_history:
            messagebox.showinfo("기록 없음", "저장된 메모 기록이 없습니다.")
            return

        dialog = Toplevel(self.root)
        dialog.title("📋 메모 기록")
        width, height = 500, 400
        dialog.geometry(f"{width}x{height}")
        dialog.resizable(True, True)
        dialog.minsize(350, 250)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        Label(dialog, text="저장된 메모를 선택하여 입력창에 불러올 수 있습니다.",
              fg="gray", font=("", 9), padx=15, pady=5).pack(anchor="w")

        # 리스트 영역
        list_frame = Frame(dialog, padx=15, pady=5)
        list_frame.pack(fill=BOTH, expand=True)

        scrollbar = Scrollbar(list_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        listbox = Listbox(list_frame, font=SYSTEM_FONT, selectmode=SINGLE,
                          yscrollcommand=scrollbar.set)
        listbox.pack(fill=BOTH, expand=True)
        scrollbar.config(command=listbox.yview)

        # 최신순으로 표시
        for entry in reversed(self._memo_history):
            preview = entry["text"][:40].replace("\n", " ")
            listbox.insert(END, f"[{entry['timestamp']}] {entry['task_count']}개 태스크 | {preview}...")

        # 버튼
        btn_frame = Frame(dialog, padx=15, pady=10)
        btn_frame.pack(fill=X, side=BOTTOM)

        def on_load():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("선택 필요", "불러올 기록을 선택해주세요.")
                return
            idx = len(self._memo_history) - 1 - sel[0]  # 역순 인덱스
            self.text_input.delete("1.0", END)
            self.text_input.insert("1.0", self._memo_history[idx]["text"])
            dialog.destroy()
            self._set_status("📋 이전 메모를 불러왔습니다")

        Button(btn_frame, text="닫기", command=dialog.destroy, width=10).pack(side=RIGHT, padx=(10, 0))
        Button(btn_frame, text="📥 입력창에 불러오기", command=on_load, width=18).pack(side=RIGHT)

    def _set_status(self, message: str):
        """상태바 메시지 업데이트"""
        self.status_var.set(message)
        self.root.update_idletasks()
    
    def _on_submit(self):
        """저장 버튼 클릭 핸들러"""
        raw_text = self.text_input.get("1.0", END).strip()
        
        if not raw_text:
            self._set_status("⚠️ 입력된 내용이 없습니다")
            return
        
        if not self.config.is_configured():
            messagebox.showwarning("설정 필요", "먼저 설정에서 API 키를 입력해주세요.")
            self._show_settings()
            return
        
        # UI 비활성화
        self.submit_btn.config(state="disabled")
        self.text_input.config(state="disabled")
        self._set_status("🔄 AI 분석 중...")
        
        # 백그라운드 처리
        thread = threading.Thread(target=self._process_async, args=(raw_text,))
        thread.daemon = True
        thread.start()
    
    def _process_async(self, raw_text: str):
        """백그라운드에서 기존 태스크 조회 → AI 분석 → 미리보기"""
        try:
            gemini = GeminiClient(self.config.get("gemini_api_key"))
            notion = NotionClient(
                self.config.get("notion_token"),
                self.config.get("notion_database_id")
            )

            # Phase 1: 기존 태스크 조회 + DB 스키마 감지
            self.root.after(0, lambda: self._set_status("🔄 기존 태스크 조회 중..."))
            try:
                existing_tasks = notion.query_database(limit=50)
            except Exception:
                existing_tasks = []
            try:
                available_props = notion.detect_available_properties()
            except Exception:
                available_props = {"tags": False, "related_tasks": False}

            # Phase 2: AI 분석 (기존 태스크 컨텍스트 포함)
            self.root.after(0, lambda: self._set_status("🔄 AI 분석 중..."))
            if existing_tasks:
                tasks = gemini.parse_memo_with_context(raw_text, existing_tasks)
            else:
                tasks = gemini.parse_memo(raw_text)

            if not tasks:
                self.root.after(0, lambda: self._on_error("AI가 태스크를 추출하지 못했습니다"))
                return

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

            self.root.after(0, lambda: self._show_preview(
                tasks, raw_text, available_props, existing_tasks
            ))

        except Exception as e:
            self.root.after(0, lambda: self._on_error(str(e)))

    def _show_preview(self, tasks: list[dict], raw_text: str,
                      available_props: dict = None, existing_pages: list[dict] = None):
        """AI 분석 결과 미리보기 다이얼로그 (맥락 연속 + 메타데이터)"""
        self.text_input.config(state="normal")
        self.submit_btn.config(state="normal")
        self._set_status(f"🔍 {len(tasks)}개 태스크 분석 완료 - 확인 후 저장하세요")

        if existing_pages is None:
            existing_pages = []
        if available_props is None:
            available_props = {}

        # 드롭다운 옵션 빌드
        page_lookup = {p["task_name"]: p["page_id"] for p in existing_pages if p["task_name"]}
        dropdown_options = ["📄 새 페이지 생성"] + [f"📎 {name}" for name in page_lookup]

        dialog = Toplevel(self.root)
        dialog.title("🔍 AI 분석 결과 확인")
        width, height = 650, 550
        dialog.geometry(f"{width}x{height}")
        dialog.resizable(True, True)
        dialog.minsize(450, 350)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        # 스크롤 가능한 컨텐츠 영역
        container = Frame(dialog)
        container.pack(fill=BOTH, expand=True, padx=15, pady=(15, 5))

        canvas = Canvas(container)
        scrollbar = Scrollbar(container, command=canvas.yview)
        scroll_frame = Frame(canvas)

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 태스크별 reasoning Entry 참조 저장
        reasoning_entries = []
        priority_colors = {"High": "#e74c3c", "Medium": "#f39c12", "Low": "#27ae60"}

        for i, task in enumerate(tasks):
            card = Frame(scroll_frame, relief="groove", bd=1, padx=10, pady=8)
            card.pack(fill=X, pady=(0, 8), padx=5)

            # 액션 드롭다운 (혼합 매칭)
            if dropdown_options:
                if task.get("_action") == "append" and task.get("related_to"):
                    default_opt = f"📎 {task['related_to']}"
                    if default_opt not in dropdown_options:
                        default_opt = "📄 새 페이지 생성"
                else:
                    default_opt = "📄 새 페이지 생성"

                action_var = StringVar(value=default_opt)
                task_ref = task  # 클로저 참조

                def make_callback(t, var):
                    def on_change(*_):
                        sel = var.get()
                        if sel == "📄 새 페이지 생성":
                            t["_action"] = "create"
                            t["_related_page_id"] = None
                            t["related_to"] = None
                        else:
                            matched = sel.replace("📎 ", "")
                            t["_action"] = "append"
                            t["_related_page_id"] = page_lookup.get(matched)
                            t["related_to"] = matched
                    return on_change

                action_var.trace_add("write", make_callback(task_ref, action_var))
                OptionMenu(card, action_var, *dropdown_options).pack(fill=X, pady=(0, 4))

            # 태스크 이름
            name = task.get("task_name", "Untitled")
            Label(card, text=f"📌 {name}", font=(SYSTEM_FONT[0], SYSTEM_FONT[1], "bold"),
                  anchor="w").pack(fill=X)
            if len(name) > 100:
                Label(card, text="⚠️ (Notion 저장 시 100자로 잘림)", fg="orange", font=("", 8)).pack(anchor="w")

            # 메타 정보
            client = task.get("client", "General")
            due = task.get("due_date") or "미지정"
            priority = task.get("priority", "Medium")
            p_color = priority_colors.get(priority, "gray")
            meta_frame = Frame(card)
            meta_frame.pack(fill=X, pady=(3, 0))
            Label(meta_frame, text=f"🏢 {client}", fg="gray", font=("", 9)).pack(side=LEFT, padx=(0, 10))
            Label(meta_frame, text=f"📅 {due}", fg="gray", font=("", 9)).pack(side=LEFT, padx=(0, 10))
            Label(meta_frame, text=f"⚡ {priority}", fg=p_color, font=("", 9, "bold")).pack(side=LEFT)

            # Tags
            tags = task.get("tags", [])
            if tags:
                tags_text = " ".join(f"#{tag}" for tag in tags)
                Label(card, text=f"🏷️ {tags_text}", fg="#8e44ad", font=("", 9),
                      anchor="w").pack(fill=X, pady=(3, 0))

            # 컨텍스트 요약
            context = task.get("context_note")
            if context:
                Label(card, text=f"📝 {context}", fg="#2c3e50", font=("", 9),
                      wraplength=550, anchor="w", justify=LEFT).pack(fill=X, pady=(3, 0))

            # 판단 근거 (AI 추출)
            reasoning = task.get("reasoning")
            if reasoning:
                Label(card, text=f"💭 {reasoning}", fg="#5d6d7e", font=("", 9),
                      wraplength=550, anchor="w", justify=LEFT).pack(fill=X, pady=(3, 0))

            # 이슈
            issue = task.get("issue")
            if issue:
                Label(card, text=f"🚨 {issue}", fg="#e74c3c", font=("", 9),
                      wraplength=550, anchor="w", justify=LEFT).pack(fill=X, pady=(3, 0))

            # 원본 텍스트 (축약)
            orig = task.get("original_text", "")
            if orig:
                display_orig = orig[:120] + "..." if len(orig) > 120 else orig
                Label(card, text=f"💬 {display_orig}", fg="gray", font=("", 8),
                      wraplength=550, anchor="w", justify=LEFT).pack(fill=X, pady=(3, 0))

            # 편집 가능한 판단 근거 입력
            reasoning_frame = Frame(card)
            reasoning_frame.pack(fill=X, pady=(4, 0))
            Label(reasoning_frame, text="💭", font=("", 9)).pack(side=LEFT)
            reasoning_var = StringVar(value=reasoning or "")
            reasoning_entry = Entry(reasoning_frame, textvariable=reasoning_var, font=("", 9))
            reasoning_entry.pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
            if not reasoning:
                reasoning_entry.insert(0, "")
                reasoning_entry.config(fg="gray")
                def on_focus_in(e, entry=reasoning_entry):
                    entry.config(fg="black")
                reasoning_entry.bind("<FocusIn>", on_focus_in)
            reasoning_entries.append((task, reasoning_var))

        # 하단 버튼
        btn_frame = Frame(dialog, padx=15, pady=10)
        btn_frame.pack(fill=X, side=BOTTOM)

        def on_save():
            # reasoning Entry 값을 태스크에 반영
            for t, var in reasoning_entries:
                val = var.get().strip()
                t["reasoning"] = val if val else None
            canvas.unbind_all("<MouseWheel>")
            dialog.destroy()
            self._confirm_and_save(tasks, raw_text, available_props)

        def on_cancel():
            canvas.unbind_all("<MouseWheel>")
            dialog.destroy()
            self._set_status("대기 중")

        Button(btn_frame, text="취소", command=on_cancel, width=10).pack(side=RIGHT, padx=(10, 0))
        Button(btn_frame, text="📤 Notion에 저장", command=on_save, width=18, font=("", 10)).pack(side=RIGHT)

        new_count = sum(1 for t in tasks if t.get("_action") != "append")
        append_count = sum(1 for t in tasks if t.get("_action") == "append")
        parts = []
        if new_count:
            parts.append(f"신규 {new_count}개")
        if append_count:
            parts.append(f"추가 {append_count}개")
        Label(btn_frame, text=f"총 {len(tasks)}개 태스크 ({', '.join(parts)})", fg="gray").pack(side=LEFT)

    def _confirm_and_save(self, tasks: list[dict], raw_text: str, available_props: dict = None):
        """미리보기 확인 후 Notion 저장 실행"""
        self.submit_btn.config(state="disabled")
        self.text_input.config(state="disabled")
        self._set_status(f"📤 Notion 전송 중... ({len(tasks)}개 태스크)")

        def save_async():
            try:
                notion = NotionClient(
                    self.config.get("notion_token"),
                    self.config.get("notion_database_id")
                )
                results = notion.save_tasks(tasks, available_props)
                created = sum(1 for r in results if r["action"] == "created")
                appended = sum(1 for r in results if r["action"] == "appended")
                self.root.after(0, lambda: self._on_success(created, appended, raw_text))
            except Exception as e:
                self.root.after(0, lambda: self._on_error(str(e)))

        thread = threading.Thread(target=save_async)
        thread.daemon = True
        thread.start()

    def _on_success(self, created: int, appended: int, raw_text: str = ""):
        """성공 처리"""
        total = created + appended
        if raw_text:
            self._memo_history.append({
                "text": raw_text,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "task_count": total
            })
            if len(self._memo_history) > self._history_max:
                self._memo_history.pop(0)

        parts = []
        if created:
            parts.append(f"신규 {created}개")
        if appended:
            parts.append(f"추가 {appended}개")
        msg = ", ".join(parts) if parts else f"{total}개"
        self._set_status(f"✅ 완료! {msg} 태스크가 Notion에 저장되었습니다")
        self.text_input.config(state="normal")
        self.text_input.delete("1.0", END)
        self.submit_btn.config(state="normal")
    
    def _on_error(self, message: str):
        """에러 처리"""
        display_msg = message if len(message) < 80 else "상세 내용은 팝업 참조"
        self._set_status(f"❌ 오류: {display_msg}")
        self.text_input.config(state="normal")
        self.submit_btn.config(state="normal")
        messagebox.showerror("오류 발생", message)
    
    def run(self):
        """애플리케이션 실행"""
        self.root.mainloop()


# ============================================================================
# 엔트리 포인트
# ============================================================================

if __name__ == "__main__":
    app = NoteAssistantApp()
    app.run()

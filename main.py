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
import uuid
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import (
    Tk, Toplevel, Label, Entry, Button, Frame, Checkbutton, BooleanVar,
    StringVar, Text, Scrollbar, messagebox, END, WORD, RIGHT, Y, BOTH, LEFT, X, TOP, BOTTOM,
    Canvas, OptionMenu, W, NW
)
from tkinter.ttk import Style, Separator, Notebook, Treeview

from google import genai
import requests

# 시스템 트레이 (선택적 의존성)
try:
    import pystray
    from pynput import keyboard as pynput_keyboard
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

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
# 히스토리 관리
# ============================================================================

HISTORY_FILE = Path(__file__).parent / "history.json"
MAX_HISTORY = 100


class HistoryManager:
    """로컬 메모 히스토리 관리"""

    def __init__(self, history_path: Path = HISTORY_FILE):
        self.history_path = history_path
        self.entries = self._load()

    def _load(self) -> list[dict]:
        if self.history_path.exists():
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save(self):
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)

    def add_entry(self, raw_text: str, parsed_tasks: list[dict],
                  sync_results: list[dict], status: str) -> str:
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "raw_text": raw_text,
            "parsed_tasks": parsed_tasks,
            "sync_results": sync_results,
            "status": status,
        }
        self.entries.insert(0, entry)
        self.entries = self.entries[:MAX_HISTORY]
        self._save()
        return entry_id

    def get_entries(self, limit: int = 20) -> list[dict]:
        return self.entries[:limit]

    def get_last_synced(self) -> dict | None:
        for entry in self.entries:
            if entry.get("status") == "synced":
                return entry
        return None

    def update_status(self, entry_id: str, status: str):
        for entry in self.entries:
            if entry.get("id") == entry_id:
                entry["status"] = status
                self._save()
                return


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
6. Keep task_name consistent for the same logical task:
   - Use the same canonical name for recurring work (e.g., always "삼성전자 재고실사" not "삼성전자 실사 작업")
   - task_name should be a stable identifier: same task mentioned differently should produce the same task_name
7. Return ONLY a valid JSON array, no markdown code blocks

OUTPUT SCHEMA (return as JSON array):
[
  {{
    "task_name": "string (명사형 종결, 50자 이내, 동일 업무는 동일 이름 유지)",
    "client": "string (회사명 or 'General')",
    "due_date": "YYYY-MM-DD or null if not specified",
    "priority": "High|Medium|Low",
    "issue": "string describing blocker or null",
    "original_text": "string (원본 메모 발췌)",
    "memo_note": "string (추가 맥락이나 업데이트 사항) or null"
  }}
]

IMPORTANT: Return ONLY the JSON array. Do not wrap in markdown code blocks."""
    
    WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.5-flash"
    
    def _build_system_prompt(self) -> str:
        """현재 날짜/시간 정보를 포함한 시스템 프롬프트 생성"""
        now = datetime.now()
        today = now.date()
        
        # 이번 주 시작(월요일)과 끝(일요일)
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        # 다음 주
        next_week_start = week_end + timedelta(days=1)
        next_week_end = next_week_start + timedelta(days=6)
        
        # 월말
        if today.month == 12:
            month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        
        return self.SYSTEM_PROMPT_TEMPLATE.format(
            current_datetime=now.strftime("%Y-%m-%d %H:%M"),
            weekday_name=self.WEEKDAYS_EN[today.weekday()],
            weekday_korean=self.WEEKDAYS_KO[today.weekday()],
            today=today.isoformat(),
            tomorrow=(today + timedelta(days=1)).isoformat(),
            day_after_tomorrow=(today + timedelta(days=2)).isoformat(),
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            next_week_start=next_week_start.isoformat(),
            next_week_end=next_week_end.isoformat(),
            month_end=month_end.isoformat()
        )
    
    def parse_memo(self, raw_text: str) -> list[dict]:
        """메모를 분석하여 구조화된 태스크 리스트 반환"""
        system_prompt = self._build_system_prompt()
        
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=f"{system_prompt}\n\nParse this memo:\n\n{raw_text}"
        )
        
        result_text = response.text.strip()
        
        # 마크다운 코드 블록 제거
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            # 첫 줄(```json)과 마지막 줄(```) 제거
            lines = [l for l in lines if not l.strip().startswith("```")]
            result_text = "\n".join(lines)
        
        try:
            tasks = json.loads(result_text)
            if isinstance(tasks, dict):
                tasks = [tasks]
            return tasks
        except json.JSONDecodeError as e:
            raise ValueError(f"AI 응답 파싱 실패: {e}\n응답: {result_text[:200]}")
    
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
    
    def _build_content_blocks(self, task: dict, is_update: bool = False) -> list[dict]:
        """태스크 정보를 Notion 콘텐츠 블록(페이지 본문)으로 변환"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        blocks = []

        if is_update:
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": f"📝 추가 메모 ({now_str})"}}]
                }
            })
        else:
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "📋 태스크 상세"}}]
                }
            })
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"생성일시: {now_str}"}, "annotations": {"color": "gray"}}]
                }
            })
            blocks.append({"object": "block", "type": "divider", "divider": {}})

        if task.get("priority") == "High":
            blocks.append({
                "object": "block", "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🚨"},
                    "rich_text": [{"type": "text", "text": {"content": f"우선순위: {task['priority']}"}}]
                }
            })

        if task.get("issue"):
            blocks.append({
                "object": "block", "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "⚠️"},
                    "rich_text": [{"type": "text", "text": {"content": f"이슈: {task['issue']}"}}]
                }
            })

        if task.get("original_text"):
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "원본 메모"}}]
                }
            })
            blocks.append({
                "object": "block", "type": "quote",
                "quote": {
                    "rich_text": [{"type": "text", "text": {"content": task["original_text"][:2000]}}]
                }
            })

        if task.get("memo_note"):
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "💬 "}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": task["memo_note"]}}
                    ]
                }
            })

        return blocks

    def _build_page_properties(self, task: dict) -> dict:
        """태스크 딕셔너리를 Notion 페이지 속성으로 변환"""
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
        
        # Due Date (선택적)
        if task.get("due_date"):
            properties["Due Date"] = {"date": {"start": task["due_date"]}}
        
        # Issue/Blocker (선택적)
        if task.get("issue"):
            properties["Issue/Blocker"] = {
                "rich_text": [{"text": {"content": task["issue"][:2000]}}]
            }
        
        return properties
    
    def create_page(self, task: dict, children: list[dict] = None) -> dict:
        """Notion 데이터베이스에 새 페이지 생성 (본문 블록 포함)"""
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_page_properties(task)
        }

        if children:
            payload["children"] = children

        response = requests.post(
            f"{self.BASE_URL}/pages",
            headers=self.headers,
            json=payload
        )

        if response.status_code != 200:
            raise Exception(f"Notion API 오류: {response.status_code} - {response.text}")

        return response.json()
    
    def query_existing_task(self, task_name: str, client: str) -> dict | None:
        """Task Name + Client 조합으로 기존 페이지 검색"""
        payload = {
            "filter": {
                "and": [
                    {"property": "Task Name", "title": {"equals": task_name}},
                    {"property": "Client", "select": {"equals": client}}
                ]
            },
            "page_size": 1
        }

        response = requests.post(
            f"{self.BASE_URL}/databases/{self.database_id}/query",
            headers=self.headers,
            json=payload
        )

        if response.status_code != 200:
            raise Exception(f"Notion 검색 오류: {response.status_code} - {response.text}")

        results = response.json().get("results", [])
        return results[0] if results else None

    def update_page_properties(self, page_id: str, task: dict) -> dict:
        """기존 페이지 속성 업데이트"""
        payload = {"properties": self._build_page_properties(task)}

        response = requests.patch(
            f"{self.BASE_URL}/pages/{page_id}",
            headers=self.headers,
            json=payload
        )

        if response.status_code != 200:
            raise Exception(f"Notion 페이지 업데이트 오류: {response.status_code} - {response.text}")

        return response.json()

    def append_page_content(self, page_id: str, blocks: list[dict]) -> dict:
        """기존 페이지에 콘텐츠 블록 추가"""
        payload = {"children": blocks}

        response = requests.patch(
            f"{self.BASE_URL}/blocks/{page_id}/children",
            headers=self.headers,
            json=payload
        )

        if response.status_code != 200:
            raise Exception(f"Notion 블록 추가 오류: {response.status_code} - {response.text}")

        return response.json()

    def sync_task(self, task: dict) -> tuple[dict, str]:
        """태스크를 Notion에 동기화 (중복 감지 → 생성 또는 업데이트)"""
        task_name = task.get("task_name", "Untitled")[:100]
        client = task.get("client", "General")

        existing = self.query_existing_task(task_name, client)

        if existing:
            page_id = existing["id"]
            self.update_page_properties(page_id, task)
            blocks = self._build_content_blocks(task, is_update=True)
            self.append_page_content(page_id, blocks)
            return existing, "updated"
        else:
            blocks = self._build_content_blocks(task, is_update=False)
            result = self.create_page(task, children=blocks)
            return result, "created"

    def sync_tasks(self, tasks: list[dict]) -> list[tuple[dict, str]]:
        """여러 태스크를 일괄 동기화"""
        results = []
        for task in tasks:
            result = self.sync_task(task)
            results.append(result)
        return results

    def archive_page(self, page_id: str) -> dict:
        """페이지 아카이브 (소프트 삭제)"""
        payload = {"archived": True}
        response = requests.patch(
            f"{self.BASE_URL}/pages/{page_id}",
            headers=self.headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Notion 아카이브 오류: {response.status_code} - {response.text}")
        return response.json()

    def query_tasks(self, filter_type: str = "all") -> list[dict]:
        """필터별 Notion DB 태스크 조회"""
        payload = {"page_size": 50}
        today = datetime.now().date().isoformat()

        if filter_type == "today":
            payload["filter"] = {"property": "Due Date", "date": {"equals": today}}
        elif filter_type == "high":
            payload["filter"] = {"property": "Priority", "select": {"equals": "High"}}
        elif filter_type == "overdue":
            payload["filter"] = {
                "and": [
                    {"property": "Due Date", "date": {"before": today}},
                    {"property": "Due Date", "date": {"is_not_empty": True}}
                ]
            }

        payload["sorts"] = [{"property": "Due Date", "direction": "ascending"}]

        response = requests.post(
            f"{self.BASE_URL}/databases/{self.database_id}/query",
            headers=self.headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Notion 조회 오류: {response.status_code} - {response.text}")

        pages = response.json().get("results", [])
        tasks = []
        for page in pages:
            props = page.get("properties", {})
            title_arr = props.get("Task Name", {}).get("title", [])
            task_name = title_arr[0]["plain_text"] if title_arr else "Untitled"
            client_obj = props.get("Client", {}).get("select")
            client = client_obj["name"] if client_obj else ""
            due_obj = props.get("Due Date", {}).get("date")
            due_date = due_obj["start"] if due_obj else ""
            priority_obj = props.get("Priority", {}).get("select")
            priority = priority_obj["name"] if priority_obj else ""

            tasks.append({
                "page_id": page["id"],
                "url": page.get("url", ""),
                "task_name": task_name,
                "client": client,
                "due_date": due_date,
                "priority": priority,
            })
        return tasks

    def test_connection(self) -> bool:
        """API 연결 및 데이터베이스 접근 테스트"""
        try:
            response = requests.get(
                f"{self.BASE_URL}/databases/{self.database_id}",
                headers=self.headers
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
        self.dialog.geometry("450x350")
        self.dialog.resizable(False, False)
        
        # 설정창을 항상 최상위로 표시 (팝업처럼 동작)
        self.dialog.attributes("-topmost", True)
        
        # 모달 다이얼로그 설정
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # 중앙 정렬
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 450) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 350) // 2
        self.dialog.geometry(f"+{x}+{y}")
        
        # 창을 앞으로 가져오고 포커스 (지연 후 다시 호출로 확실히 포커스)
        self.dialog.lift()
        self.dialog.focus_force()
        self.dialog.after(10, lambda: self.dialog.focus_force())
        
        self._create_widgets()
    
    def _create_widgets(self):
        main_frame = Frame(self.dialog, padx=20, pady=20)
        main_frame.pack(fill=BOTH, expand=True)
        
        # Gemini API Key
        Label(main_frame, text="Gemini API Key:", anchor="w").pack(fill=X, pady=(0, 5))
        self.gemini_key_var = StringVar(value=self.config.get("gemini_api_key", ""))
        Entry(main_frame, textvariable=self.gemini_key_var, show="*", width=50).pack(fill=X, pady=(0, 15))
        
        # Notion Token
        Label(main_frame, text="Notion Integration Token:", anchor="w").pack(fill=X, pady=(0, 5))
        self.notion_token_var = StringVar(value=self.config.get("notion_token", ""))
        Entry(main_frame, textvariable=self.notion_token_var, show="*", width=50).pack(fill=X, pady=(0, 15))
        
        # Notion Database ID
        Label(main_frame, text="Notion Database ID:", anchor="w").pack(fill=X, pady=(0, 5))
        self.notion_db_var = StringVar(value=self.config.get("notion_database_id", ""))
        Entry(main_frame, textvariable=self.notion_db_var, width=50).pack(fill=X, pady=(0, 20))
        
        # 버튼 프레임
        btn_frame = Frame(main_frame)
        btn_frame.pack(fill=X)
        
        Button(btn_frame, text="🔗 연결 테스트", command=self._test_connections, width=15).pack(side=LEFT)
        Button(btn_frame, text="취소", command=self.dialog.destroy, width=10).pack(side=RIGHT, padx=(10, 0))
        Button(btn_frame, text="💾 저장", command=self._save, width=10).pack(side=RIGHT)
    
    def _test_connections(self):
        """API 연결 테스트"""
        results = []
        
        # Gemini 테스트
        gemini_key = self.gemini_key_var.get().strip()
        if gemini_key:
            try:
                client = GeminiClient(gemini_key)
                if client.test_connection():
                    results.append("✅ Gemini API: 연결 성공")
                else:
                    results.append("❌ Gemini API: 연결 실패")
            except Exception as e:
                results.append(f"❌ Gemini API: {str(e)[:50]}")
        else:
            results.append("⚠️ Gemini API Key가 입력되지 않았습니다")
        
        # Notion 테스트
        notion_token = self.notion_token_var.get().strip()
        notion_db = self.notion_db_var.get().strip()
        if notion_token and notion_db:
            try:
                client = NotionClient(notion_token, notion_db)
                if client.test_connection():
                    results.append("✅ Notion API: 연결 성공")
                else:
                    results.append("❌ Notion API: 연결 실패 (토큰 또는 DB ID 확인)")
            except Exception as e:
                results.append(f"❌ Notion API: {str(e)[:50]}")
        else:
            results.append("⚠️ Notion Token 또는 Database ID가 입력되지 않았습니다")
        
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
# AI 분석 결과 미리보기 다이얼로그
# ============================================================================

class PreviewDialog:
    """AI 분석 결과 미리보기 및 편집 다이얼로그"""

    PRIORITY_OPTIONS = ["High", "Medium", "Low"]

    def __init__(self, parent, tasks: list[dict], on_confirm, on_cancel):
        self.original_tasks = tasks
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.task_entries = []

        self.dialog = Toplevel(parent)
        self.dialog.title(f"AI 분석 결과 미리보기 — {len(tasks)}개 태스크")
        width, height = 500, min(200 + len(tasks) * 170, 600)
        self.dialog.geometry(f"{width}x{height}")
        self.dialog.resizable(True, True)
        self.dialog.minsize(400, 300)
        self.dialog.attributes("-topmost", True)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 중앙 정렬
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.lift()
        self.dialog.focus_force()

        self._create_widgets()
        self._bind_shortcuts()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel_click)

    def _create_widgets(self):
        # 스크롤 가능한 태스크 목록
        container = Frame(self.dialog)
        container.pack(fill=BOTH, expand=True, padx=10, pady=(10, 0))

        canvas = Canvas(container, highlightthickness=0)
        scrollbar = Scrollbar(container, command=canvas.yview)
        self.inner_frame = Frame(canvas)

        self.inner_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.inner_frame, anchor=NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)

        # 태스크별 편집 폼 생성
        for i, task in enumerate(self.original_tasks):
            self._create_task_form(i, task)

        # 하단 버튼
        btn_frame = Frame(self.dialog, padx=10, pady=10)
        btn_frame.pack(fill=X)

        Button(btn_frame, text="취소 (Esc)", command=self._on_cancel_click, width=12).pack(side=RIGHT, padx=(5, 0))
        Button(btn_frame, text="전송 (Ctrl+Enter)", command=self._on_confirm_click, width=16).pack(side=RIGHT)

    def _create_task_form(self, index: int, task: dict):
        """태스크 하나에 대한 편집 폼 생성"""
        frame = Frame(self.inner_frame, relief="groove", bd=1, padx=10, pady=8)
        frame.pack(fill=X, pady=(0, 8))

        Label(frame, text=f"태스크 {index + 1}", font=("", 10, "bold")).pack(anchor=W)

        entries = {}
        fields = [
            ("태스크명", "task_name"),
            ("고객사", "client"),
            ("마감일", "due_date"),
            ("이슈", "issue"),
        ]
        for label_text, key in fields:
            row = Frame(frame)
            row.pack(fill=X, pady=1)
            Label(row, text=f"{label_text}:", width=8, anchor=W).pack(side=LEFT)
            var = StringVar(value=task.get(key) or "")
            Entry(row, textvariable=var, font=SYSTEM_FONT).pack(side=LEFT, fill=X, expand=True)
            entries[key] = var

        # 우선순위 OptionMenu
        row = Frame(frame)
        row.pack(fill=X, pady=1)
        Label(row, text="우선순위:", width=8, anchor=W).pack(side=LEFT)
        priority_var = StringVar(value=task.get("priority", "Medium"))
        OptionMenu(row, priority_var, *self.PRIORITY_OPTIONS).pack(side=LEFT)
        entries["priority"] = priority_var

        self.task_entries.append(entries)

    def _bind_shortcuts(self):
        self.dialog.bind("<Control-Return>", lambda e: self._on_confirm_click())
        self.dialog.bind("<Command-Return>", lambda e: self._on_confirm_click())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel_click())

    def _on_confirm_click(self):
        """편집된 태스크를 수집하여 콜백 호출"""
        edited_tasks = []
        for i, entries in enumerate(self.task_entries):
            task = {
                "task_name": entries["task_name"].get().strip(),
                "client": entries["client"].get().strip() or "General",
                "due_date": entries["due_date"].get().strip() or None,
                "priority": entries["priority"].get(),
                "issue": entries["issue"].get().strip() or None,
                "original_text": self.original_tasks[i].get("original_text", ""),
                "memo_note": self.original_tasks[i].get("memo_note"),
            }
            if task["task_name"]:
                edited_tasks.append(task)

        self.dialog.destroy()
        if edited_tasks:
            self.on_confirm(edited_tasks)
        else:
            self.on_cancel()

    def _on_cancel_click(self):
        self.dialog.destroy()
        self.on_cancel()


# ============================================================================
# 히스토리 다이얼로그
# ============================================================================

class HistoryDialog:
    """메모 히스토리 조회/재전송/실행취소 다이얼로그"""

    STATUS_LABELS = {
        "synced": "✅", "failed": "❌", "cancelled": "⏹", "undone": "↩️"
    }

    def __init__(self, parent, history: HistoryManager, notion_config: dict,
                 on_resend):
        self.history = history
        self.notion_config = notion_config
        self.on_resend = on_resend

        self.dialog = Toplevel(parent)
        self.dialog.title("📋 히스토리")
        self.dialog.geometry("550x400")
        self.dialog.resizable(True, True)
        self.dialog.minsize(450, 300)
        self.dialog.attributes("-topmost", True)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 550) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 400) // 2
        self.dialog.geometry(f"+{x}+{y}")
        self.dialog.lift()
        self.dialog.focus_force()

        self._create_widgets()
        self._refresh()

    def _create_widgets(self):
        # Treeview
        tree_frame = Frame(self.dialog, padx=10, pady=10)
        tree_frame.pack(fill=BOTH, expand=True)

        columns = ("timestamp", "memo", "status")
        self.tree = Treeview(tree_frame, columns=columns, show="headings", height=12)
        self.tree.heading("timestamp", text="시각")
        self.tree.heading("memo", text="메모")
        self.tree.heading("status", text="상태")
        self.tree.column("timestamp", width=130, minwidth=100)
        self.tree.column("memo", width=320, minwidth=200)
        self.tree.column("status", width=50, minwidth=40, anchor="center")

        scrollbar = Scrollbar(tree_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        # 버튼
        btn_frame = Frame(self.dialog, padx=10, pady=10)
        btn_frame.pack(fill=X)

        Button(btn_frame, text="닫기", command=self.dialog.destroy, width=10).pack(side=RIGHT)
        Button(btn_frame, text="실행취소", command=self._on_undo, width=10).pack(side=RIGHT, padx=5)
        Button(btn_frame, text="다시 보내기", command=self._on_resend, width=12).pack(side=RIGHT, padx=5)

    def _refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for entry in self.history.get_entries(50):
            ts = entry.get("timestamp", "")[:16].replace("T", " ")
            memo = entry.get("raw_text", "")[:40]
            status = self.STATUS_LABELS.get(entry.get("status", ""), "?")
            self.tree.insert("", END, iid=entry["id"], values=(ts, memo, status))

    def _get_selected_entry(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("선택 필요", "항목을 선택해주세요.", parent=self.dialog)
            return None
        entry_id = selection[0]
        for entry in self.history.entries:
            if entry.get("id") == entry_id:
                return entry
        return None

    def _on_resend(self):
        entry = self._get_selected_entry()
        if entry:
            self.dialog.destroy()
            self.on_resend(entry.get("raw_text", ""))

    def _on_undo(self):
        last = self.history.get_last_synced()
        if not last:
            messagebox.showinfo("실행취소", "취소할 항목이 없습니다.", parent=self.dialog)
            return

        sync_results = last.get("sync_results", [])
        created_pages = [r for r in sync_results if r.get("action") == "created"]

        if not created_pages:
            messagebox.showinfo("실행취소", "생성된 페이지가 없어 취소할 수 없습니다.",
                                parent=self.dialog)
            return

        task_names = ", ".join(r.get("task_name", "?") for r in created_pages)
        if not messagebox.askyesno(
            "실행취소 확인",
            f"다음 페이지를 아카이브합니까?\n{task_names}",
            parent=self.dialog
        ):
            return

        try:
            notion = NotionClient(
                self.notion_config["token"],
                self.notion_config["database_id"]
            )
            for result in created_pages:
                page_id = result.get("page_id")
                if page_id:
                    notion.archive_page(page_id)

            self.history.update_status(last["id"], "undone")
            self._refresh()
            messagebox.showinfo("완료", "페이지가 아카이브되었습니다.", parent=self.dialog)
        except Exception as e:
            messagebox.showerror("오류", str(e), parent=self.dialog)


# ============================================================================
# 시스템 트레이 (pystray/pynput 설치 시 활성화)
# ============================================================================

class TrayManager:
    """시스템 트레이 아이콘 및 글로벌 핫키 관리"""

    DEFAULT_HOTKEY = "<ctrl>+<shift>+n"

    def __init__(self, app):
        self.app = app
        self.icon = None
        self.hotkey_listener = None
        self._visible = True

    def _create_icon_image(self):
        """트레이 아이콘 이미지 생성 (16x16 메모 아이콘)"""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([8, 4, 56, 60], radius=6, fill="#4A90D9")
        draw.rectangle([18, 16, 46, 20], fill="white")
        draw.rectangle([18, 26, 42, 30], fill="white")
        draw.rectangle([18, 36, 38, 40], fill="white")
        return img

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("열기", self.show_window, default=True),
            pystray.MenuItem("설정", lambda: self.app.root.after(0, self.app._show_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._quit),
        )

    def start(self):
        """트레이 아이콘 및 글로벌 핫키 시작"""
        self.icon = pystray.Icon(
            "audit-note",
            self._create_icon_image(),
            "Audit Note Assistant",
            self._build_menu()
        )

        # 글로벌 핫키
        hotkey = self.app.config.get("global_hotkey", self.DEFAULT_HOTKEY)
        try:
            self.hotkey_listener = pynput_keyboard.GlobalHotKeys({
                hotkey: self.toggle_window
            })
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
        except Exception:
            pass  # 핫키 등록 실패 시 무시

        # 트레이 아이콘을 별도 스레드에서 실행
        tray_thread = threading.Thread(target=self.icon.run, daemon=True)
        tray_thread.start()

    def stop(self):
        """트레이 아이콘 및 핫키 정리"""
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        if self.icon:
            self.icon.stop()

    def show_window(self, *args):
        """창 표시"""
        self._visible = True
        self.app.root.after(0, self._do_show)

    def _do_show(self):
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()

    def hide_window(self):
        """창 숨기기"""
        self._visible = False
        self.app.root.after(0, self.app.root.withdraw)

    def toggle_window(self):
        """창 표시/숨기기 토글"""
        if self._visible:
            self.hide_window()
        else:
            self.show_window()

    def _quit(self, *args):
        """앱 종료"""
        self.stop()
        self.app.root.after(0, self.app.root.destroy)


# ============================================================================
# 메인 애플리케이션
# ============================================================================

class AuditNoteApp:
    """AI Audit Note Assistant 메인 앱"""

    def __init__(self):
        self.config = ConfigManager()
        self.history = HistoryManager()
        self._current_raw_text = ""

        # 메인 윈도우 설정
        self.root = Tk()
        self.root.title("📝 Audit Note Assistant")
        self.root.geometry("500x550")
        self.root.minsize(400, 450)

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
        Button(toolbar, text="📋 히스토리", command=self._show_history).pack(side=LEFT, padx=5)

        Checkbutton(
            toolbar, text="📌 항상 위",
            variable=self.always_on_top_var,
            command=self._toggle_topmost
        ).pack(side=RIGHT)

        # 구분선
        Separator(self.root, orient="horizontal").pack(fill=X, padx=10)

        # Notebook (탭)
        self.notebook = Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=5, pady=5)

        # 탭 1: 메모 입력
        input_tab = Frame(self.notebook)
        self.notebook.add(input_tab, text="📝 메모 입력")
        self._create_input_tab(input_tab)

        # 탭 2: 대시보드
        dashboard_tab = Frame(self.notebook)
        self.notebook.add(dashboard_tab, text="📊 대시보드")
        self._create_dashboard_tab(dashboard_tab)

        # 대시보드 탭 전환 시 자동 새로고침
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # 상태바
        self.status_var = StringVar(value="대기 중")
        status_bar = Frame(self.root, padx=10, pady=5, relief="sunken", bd=1)
        status_bar.pack(fill=X, side=BOTTOM)
        Label(status_bar, textvariable=self.status_var, anchor="w").pack(fill=X)

    def _create_input_tab(self, parent):
        """메모 입력 탭 UI"""
        hint_frame = Frame(parent, padx=10, pady=5)
        hint_frame.pack(fill=X)
        Label(
            hint_frame,
            text="💡 업무 메모를 입력하고 Ctrl+Enter로 저장하세요",
            fg="gray", font=("", 9)
        ).pack(anchor="w")

        text_frame = Frame(parent, padx=10, pady=5)
        text_frame.pack(fill=BOTH, expand=True)

        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.text_input = Text(
            text_frame, wrap=WORD, font=SYSTEM_FONT,
            yscrollcommand=scrollbar.set, padx=10, pady=10
        )
        self.text_input.pack(fill=BOTH, expand=True)
        scrollbar.config(command=self.text_input.yview)

        btn_frame = Frame(parent, padx=10, pady=10)
        btn_frame.pack(fill=X)

        self.submit_btn = Button(
            btn_frame, text="📤 저장 (Ctrl+Enter)",
            command=self._on_submit, font=("", 10), height=2
        )
        self.submit_btn.pack(fill=X)

    def _create_dashboard_tab(self, parent):
        """대시보드 탭 UI"""
        # 필터 버튼
        filter_frame = Frame(parent, padx=10, pady=8)
        filter_frame.pack(fill=X)

        self._dashboard_filter = StringVar(value="all")
        filters = [("전체", "all"), ("오늘 마감", "today"), ("High", "high"), ("지연", "overdue")]
        for label, value in filters:
            Button(
                filter_frame, text=label,
                command=lambda v=value: self._refresh_dashboard(v)
            ).pack(side=LEFT, padx=2)

        Button(filter_frame, text="🔄 새로고침",
               command=lambda: self._refresh_dashboard(self._dashboard_filter.get())
               ).pack(side=RIGHT)

        # Treeview
        tree_frame = Frame(parent, padx=10, pady=5)
        tree_frame.pack(fill=BOTH, expand=True)

        columns = ("task_name", "client", "due_date", "priority")
        self.dashboard_tree = Treeview(tree_frame, columns=columns, show="headings", height=15)
        self.dashboard_tree.heading("task_name", text="Task Name")
        self.dashboard_tree.heading("client", text="Client")
        self.dashboard_tree.heading("due_date", text="Due Date")
        self.dashboard_tree.heading("priority", text="Priority")
        self.dashboard_tree.column("task_name", width=200, minwidth=120)
        self.dashboard_tree.column("client", width=100, minwidth=60)
        self.dashboard_tree.column("due_date", width=90, minwidth=70)
        self.dashboard_tree.column("priority", width=70, minwidth=50)

        tree_scroll = Scrollbar(tree_frame, command=self.dashboard_tree.yview)
        self.dashboard_tree.configure(yscrollcommand=tree_scroll.set)
        self.dashboard_tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_scroll.pack(side=RIGHT, fill=Y)

        self.dashboard_tree.bind("<Double-1>", self._on_dashboard_double_click)
        self._dashboard_pages = {}  # page_id -> url mapping
    
    def _bind_shortcuts(self):
        """키보드 단축키 바인딩"""
        self.root.bind("<Control-Return>", lambda e: self._on_submit())
        self.root.bind("<Command-Return>", lambda e: self._on_submit())  # macOS
        self.root.bind("<Control-q>", lambda e: self._quit())

        # 시스템 트레이 설정
        self.tray = None
        if HAS_TRAY:
            self.tray = TrayManager(self)
            self.root.protocol("WM_DELETE_WINDOW", self._on_close_to_tray)
        else:
            self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
    
    def _update_topmost(self):
        """창 최상위 설정 업데이트"""
        self.root.attributes("-topmost", self.always_on_top_var.get())
    
    def _toggle_topmost(self):
        """Always on Top 토글"""
        self._update_topmost()
        self.config.set("always_on_top", self.always_on_top_var.get())
        self.config.save()
    
    def _on_tab_changed(self, event):
        """탭 전환 이벤트 핸들러"""
        selected = self.notebook.index(self.notebook.select())
        if selected == 1:  # 대시보드 탭
            self._refresh_dashboard(self._dashboard_filter.get())

    def _refresh_dashboard(self, filter_type: str = "all"):
        """대시보드 데이터 새로고침"""
        if not self.config.is_configured():
            self._set_status("⚠️ 설정에서 API 키를 먼저 입력하세요")
            return

        self._dashboard_filter.set(filter_type)
        self._set_status(f"🔄 대시보드 로딩 중...")

        thread = threading.Thread(target=self._dashboard_load_async, args=(filter_type,))
        thread.daemon = True
        thread.start()

    def _dashboard_load_async(self, filter_type: str):
        """백그라운드에서 Notion 대시보드 데이터 로드"""
        try:
            notion = NotionClient(
                self.config.get("notion_token"),
                self.config.get("notion_database_id")
            )
            tasks = notion.query_tasks(filter_type)
            self.root.after(0, lambda: self._on_dashboard_loaded(tasks))
        except Exception as e:
            self.root.after(0, lambda: self._set_status(f"❌ 대시보드 오류: {str(e)[:50]}"))

    def _on_dashboard_loaded(self, tasks: list[dict]):
        """대시보드 데이터 로드 완료"""
        for item in self.dashboard_tree.get_children():
            self.dashboard_tree.delete(item)
        self._dashboard_pages = {}

        for task in tasks:
            iid = task["page_id"]
            self.dashboard_tree.insert("", END, iid=iid, values=(
                task["task_name"], task["client"],
                task["due_date"], task["priority"]
            ))
            self._dashboard_pages[iid] = task.get("url", "")

        self._set_status(f"📊 대시보드: {len(tasks)}개 태스크")

    def _on_dashboard_double_click(self, event):
        """대시보드 항목 더블클릭 → 브라우저에서 열기"""
        selection = self.dashboard_tree.selection()
        if selection:
            url = self._dashboard_pages.get(selection[0], "")
            if url:
                webbrowser.open(url)

    def _show_settings(self):
        """설정 다이얼로그 표시"""
        SettingsDialog(self.root, self.config, self._on_settings_saved)

    def _show_history(self):
        """히스토리 다이얼로그 표시"""
        HistoryDialog(
            self.root, self.history,
            {"token": self.config.get("notion_token"),
             "database_id": self.config.get("notion_database_id")},
            on_resend=self._on_history_resend
        )

    def _on_history_resend(self, raw_text: str):
        """히스토리에서 재전송 선택 시 텍스트 복원"""
        self.notebook.select(0)  # 메모 입력 탭으로 전환
        self.text_input.delete("1.0", END)
        self.text_input.insert("1.0", raw_text)
        self._set_status("📋 히스토리에서 메모를 복원했습니다")

    def _on_settings_saved(self):
        """설정 저장 후 콜백"""
        pass  # 필요시 클라이언트 재초기화
    
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
        """백그라운드에서 AI 분석 후 미리보기로 전환"""
        try:
            gemini = GeminiClient(self.config.get("gemini_api_key"))
            tasks = gemini.parse_memo(raw_text)

            if not tasks:
                self.root.after(0, lambda: self._on_error("AI가 태스크를 추출하지 못했습니다"))
                return

            # 메인 스레드에서 미리보기 다이얼로그 표시
            self.root.after(0, lambda: self._show_preview(tasks, raw_text))

        except Exception as e:
            self.root.after(0, lambda: self._on_error(str(e)))

    def _show_preview(self, tasks: list[dict], raw_text: str):
        """AI 분석 결과 미리보기 다이얼로그 표시"""
        self._set_status(f"👀 미리보기 — {len(tasks)}개 태스크 확인 중")
        self._current_raw_text = raw_text
        PreviewDialog(
            self.root, tasks,
            on_confirm=self._sync_to_notion,
            on_cancel=self._on_preview_cancel
        )

    def _on_preview_cancel(self):
        """미리보기 취소 시 UI 복원"""
        self._set_status("취소됨")
        self.text_input.config(state="normal")
        self.submit_btn.config(state="normal")

    def _sync_to_notion(self, tasks: list[dict]):
        """미리보기 확인 후 Notion 동기화 시작"""
        self._set_status(f"📤 Notion 동기화 중... ({len(tasks)}개 태스크)")
        thread = threading.Thread(
            target=self._notion_sync_async,
            args=(tasks, self._current_raw_text)
        )
        thread.daemon = True
        thread.start()

    def _notion_sync_async(self, tasks: list[dict], raw_text: str):
        """백그라운드에서 Notion 동기화"""
        try:
            notion = NotionClient(
                self.config.get("notion_token"),
                self.config.get("notion_database_id")
            )
            results = notion.sync_tasks(tasks)

            created = sum(1 for _, action in results if action == "created")
            updated = sum(1 for _, action in results if action == "updated")

            # 히스토리 기록
            sync_results = []
            for (result, action), task in zip(results, tasks):
                sync_results.append({
                    "page_id": result.get("id", ""),
                    "action": action,
                    "task_name": task.get("task_name", "")
                })
            self.history.add_entry(raw_text, tasks, sync_results, "synced")

            self.root.after(0, lambda: self._on_success(created, updated))

        except Exception as e:
            # 실패 시에도 히스토리 기록
            self.history.add_entry(raw_text, tasks, [], "failed")
            self.root.after(0, lambda: self._on_error(str(e)))
    
    def _on_success(self, created: int, updated: int):
        """성공 처리 (생성/업데이트 구분 표시)"""
        parts = []
        if created > 0:
            parts.append(f"생성 {created}개")
        if updated > 0:
            parts.append(f"업데이트 {updated}개")
        summary = ", ".join(parts) if parts else "처리 완료"

        self._set_status(f"✅ 완료! {summary}")
        self.text_input.config(state="normal")
        self.text_input.delete("1.0", END)
        self.submit_btn.config(state="normal")
    
    def _on_error(self, message: str):
        """에러 처리"""
        self._set_status(f"❌ 오류: {message[:50]}...")
        self.text_input.config(state="normal")
        self.submit_btn.config(state="normal")
        messagebox.showerror("오류 발생", message)
    
    def _on_close_to_tray(self):
        """창 닫기 → 트레이로 최소화"""
        if self.tray:
            self.tray.hide_window()
        else:
            self.root.destroy()

    def _quit(self):
        """앱 완전 종료"""
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        """애플리케이션 실행"""
        if self.tray:
            self.tray.start()
        self.root.mainloop()


# ============================================================================
# 엔트리 포인트
# ============================================================================

if __name__ == "__main__":
    app = AuditNoteApp()
    app.run()

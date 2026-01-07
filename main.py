"""
AI Audit Note Assistant
회계법인 감사팀을 위한 메모 자동화 데스크톱 애플리케이션

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
    StringVar, Text, Scrollbar, messagebox, END, WORD, RIGHT, Y, BOTH, LEFT, X, TOP, BOTTOM
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
    
    def create_page(self, task: dict) -> dict:
        """Notion 데이터베이스에 새 페이지 생성"""
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_page_properties(task)
        }
        
        response = requests.post(
            f"{self.BASE_URL}/pages",
            headers=self.headers,
            json=payload
        )
        
        if response.status_code != 200:
            raise Exception(f"Notion API 오류: {response.status_code} - {response.text}")
        
        return response.json()
    
    def create_pages(self, tasks: list[dict]) -> list[dict]:
        """여러 태스크를 Notion에 일괄 생성"""
        results = []
        for task in tasks:
            result = self.create_page(task)
            results.append(result)
        return results
    
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
# 메인 애플리케이션
# ============================================================================

class AuditNoteApp:
    """AI Audit Note Assistant 메인 앱"""
    
    def __init__(self):
        self.config = ConfigManager()
        
        # 메인 윈도우 설정
        self.root = Tk()
        self.root.title("📝 Audit Note Assistant")
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
        """백그라운드에서 AI 분석 및 Notion 전송"""
        try:
            # 1. Gemini API 호출
            gemini = GeminiClient(self.config.get("gemini_api_key"))
            tasks = gemini.parse_memo(raw_text)
            
            if not tasks:
                self.root.after(0, lambda: self._on_error("AI가 태스크를 추출하지 못했습니다"))
                return
            
            self.root.after(0, lambda: self._set_status(f"📤 Notion 전송 중... ({len(tasks)}개 태스크)"))
            
            # 2. Notion API 호출
            notion = NotionClient(
                self.config.get("notion_token"),
                self.config.get("notion_database_id")
            )
            notion.create_pages(tasks)
            
            # 성공
            self.root.after(0, lambda: self._on_success(len(tasks)))
            
        except Exception as e:
            self.root.after(0, lambda: self._on_error(str(e)))
    
    def _on_success(self, count: int):
        """성공 처리"""
        self._set_status(f"✅ 완료! {count}개 태스크가 Notion에 저장되었습니다")
        self.text_input.config(state="normal")
        self.text_input.delete("1.0", END)
        self.submit_btn.config(state="normal")
    
    def _on_error(self, message: str):
        """에러 처리"""
        self._set_status(f"❌ 오류: {message[:50]}...")
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
    app = AuditNoteApp()
    app.run()

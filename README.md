# 📝 AI Note Assistant

**AI 기반 메모 자동화** 데스크톱 애플리케이션

업무 메모를 입력하면 AI가 자동으로 구조화하여 Notion 데이터베이스에 적재합니다.

## ✨ Features

- 🤖 **AI 메모 분석** - Google Gemini AI가 메모를 분석하여 태스크, 마감일, 우선순위, 이슈 자동 추출
- 📅 **자연어 날짜 변환** - "다음 주 월요일", "월말" 등 자연어를 실제 날짜로 변환
- 📤 **Notion 자동 적재** - 추출된 태스크를 Notion 데이터베이스에 자동 저장
- 📌 **Always on Top** - 다른 작업 중에도 항상 화면 위에 표시

## 🖼️ Screenshot

```
┌─────────────────────────────────────┐
│ ⚙️ 설정                    📌 항상 위 │
├─────────────────────────────────────┤
│ 💡 업무 메모를 입력하고 Ctrl+Enter로  │
│    저장하세요                         │
├─────────────────────────────────────┤
│                                     │
│  삼성전자 재고실사 다음주 화요일까지  │
│  완료해야함. 연락두절된 담당자 있음    │
│                                     │
├─────────────────────────────────────┤
│       [ 📤 저장 (Ctrl+Enter) ]       │
├─────────────────────────────────────┤
│ ✅ 완료! 1개 태스크가 저장되었습니다   │
└─────────────────────────────────────┘
```

## 🚀 Quick Start

### 1. 의존성 설치

```bash
# Python 3.10+ 권장 (tkinter 포함 필수)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 실행

```bash
python main.py
```

### 3. 설정

앱 실행 후 ⚙️ 설정 버튼을 클릭하여:
- **Gemini API Key** 입력 ([Google AI Studio](https://aistudio.google.com/)에서 발급)
- **Notion Integration Token** 입력 ([Notion Integrations](https://www.notion.so/my-integrations)에서 생성)
- **Notion Database ID** 입력 (데이터베이스 URL에서 추출)

## 📋 Notion Database Schema

다음 속성을 가진 Notion 데이터베이스를 생성하세요:

| Property Name | Type | Description |
|---------------|------|-------------|
| `Task Name` | Title | 태스크 이름 |
| `Client` | Select | 회사/고객명 |
| `Due Date` | Date | 마감일 |
| `Priority` | Select | High / Medium / Low |
| `Issue/Blocker` | Rich Text | 이슈/블로커 |
| `Original Text` | Rich Text | 원본 메모 |

> ⚠️ Notion Integration을 해당 데이터베이스에 연결(Share)해야 합니다.

## 💡 Usage Examples

### 입력 예시

```
삼성전자 재고실사 다음주 화요일까지 완료해야함
담당자 연락두절 상태
```

### AI 변환 결과

```json
{
  "task_name": "삼성전자 재고실사 완료",
  "client": "삼성전자",
  "due_date": "2026-01-13",
  "priority": "High",
  "issue": "담당자 연락두절",
  "original_text": "삼성전자 재고실사 다음주 화요일까지..."
}
```

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` (Windows/Linux) | 저장 |
| `Cmd+Enter` (macOS) | 저장 |

## 🛠️ Tech Stack

- **Python 3.10+**
- **Tkinter** - 크로스 플랫폼 GUI
- **Google Gemini API** - AI 메모 분석
- **Notion API** - 데이터베이스 연동

## 📁 Project Structure

```
.
├── main.py           # 메인 애플리케이션
├── config.json       # 설정 파일 (자동 생성)
├── requirements.txt  # Python 의존성
└── README.md
```

## 📄 License

MIT License

## 🤝 Contributing

Issues와 Pull Requests를 환영합니다!

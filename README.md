# AI Note Assistant

**개인화된 업무 데이터베이스를 구축하는 AI 메모 도구**

단순한 메모 기록이 아닌, AI가 메모를 분석하고 구조화하여 **맥락이 연결된 개인 지식 데이터베이스**를 Notion 위에 자동으로 구축합니다. 축적된 데이터는 태그, 관계, 타임라인으로 서로 연결되어 검색 가능하고 추적 가능한 업무 기록이 됩니다.

## 왜 이 도구인가

일반적인 메모 앱은 기록은 쉽지만 **데이터 간 연결성이 없습니다**. 시간이 지나면 메모는 쌓이지만 맥락은 사라집니다.

AI Note Assistant는 다르게 접근합니다:

- **메모를 구조화된 데이터로 변환** — 고객명, 마감일, 우선순위, 이슈를 자동 추출
- **기존 맥락과 자동 연결** — 후속 메모는 기존 태스크에 타임라인으로 추가
- **판단 근거를 보존** — "왜 이 결정을 했는가"라는 맥락을 AI가 억지로 만들지 않고, 메모 속 유저의 원래 판단 근거를 그대로 기록
- **태그와 관계로 크로스 레퍼런스** — 모든 페이지가 키워드 태그와 관련 태스크 링크로 연결
- **데스크톱 + 텔레그램** — PC에서도, 이동 중에도 동일한 파이프라인으로 기록

결과적으로 Notion에 축적되는 것은 단순 메모가 아니라, 검색 가능하고 필터링 가능한 **구조화된 업무 데이터베이스**입니다. 이 데이터는 Notion의 내보내기 기능을 통해 Markdown/CSV로 추출하여 다른 도구에서 활용하거나 로컬 백업으로 보관할 수 있습니다.

---

## 주요 기능

### AI 메모 분석
- Google Gemini AI가 자유 텍스트 메모에서 태스크, 고객명, 마감일, 우선순위, 이슈를 자동 추출
- "다음 주 월요일", "월말" 등 자연어를 실제 날짜로 변환
- 하나의 메모에서 복수 태스크 동시 추출 가능

### 맥락 연속 메모 (Contextual Continuation)
- 새 메모를 작성할 때 AI가 기존 Notion DB의 태스크를 참조
- 후속 메모가 감지되면 새 페이지를 만들지 않고 **기존 페이지에 타임라인 형태로 추가**
- 시점(timestamp)과 맥락이 자연스럽게 이어지는 업무 히스토리 구축
- AI 추천 + 사용자 수동 선택 하이브리드 매칭 (데스크톱 드롭다운 / 텔레그램 미리보기)

### 메타데이터 자동 생성
- **Tags** — AI가 메모에서 2~5개 키워드를 추출하여 multi-select 태그 자동 부여
- **Related Tasks** — 관련 태스크 간 Notion relation 자동 연결
- **Context Summary** — 이번 메모의 핵심 업데이트를 한 줄로 요약
- **Reasoning (판단 근거)** — 메모 속에 담긴 유저의 의사결정 배경을 원문 그대로 보존. AI가 만들어내지 않음. 명시적 근거가 없으면 생략

### 미리보기 & 확인
- Notion에 저장하기 전에 AI 분석 결과를 미리보기로 확인
- 태스크별로 신규 생성 / 기존 페이지 추가를 선택 가능
- 판단 근거를 직접 입력하거나 수정 가능

### 입력 채널

| 채널 | 실행 방법 | 특징 |
|------|----------|------|
| **데스크톱 GUI** | `python main.py` | Tkinter 기반, Always-on-Top, 드롭다운 매칭, 메모 히스토리 |
| **텔레그램 봇** | `python telegram_bot.py` | 모바일/이동 중 입력, 인라인 버튼 저장/취소, 사용자 접근 제어 |

두 채널 모두 동일한 AI 분석 + Notion 저장 파이프라인을 사용합니다.

---

## 빠른 시작

### 1. 의존성 설치

```bash
# Python 3.10+ 권장 (tkinter 포함 필수)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Notion 데이터베이스 준비

[Notion](https://www.notion.so)에서 아래 스키마로 데이터베이스를 생성합니다.

**필수 속성:**

| Property Name | Type | 설명 |
|---------------|------|------|
| `Task Name` | Title | 태스크 이름 (명사형, 50자 이내) |
| `Client` | Select | 회사/고객명 |
| `Due Date` | Date | 마감일 |
| `Priority` | Select | `High` / `Medium` / `Low` |
| `Issue/Blocker` | Rich Text | 이슈 및 블로커 |
| `Original Text` | Rich Text | 원본 메모 텍스트 |

**선택 속성 (추가하면 자동 활용됨):**

| Property Name | Type | 설명 |
|---------------|------|------|
| `Tags` | Multi-select | AI 자동 생성 키워드 태그 |
| `Related Tasks` | Relation (같은 DB) | 관련 태스크 간 연결 링크 |

> Tags와 Related Tasks가 없어도 정상 동작합니다. 앱이 DB 스키마를 자동 감지하여 존재하는 속성만 사용합니다.

### 3. API 키 발급

- **Gemini API Key**: [Google AI Studio](https://aistudio.google.com/) → API Key 생성
- **Notion Integration Token**: [Notion Integrations](https://www.notion.so/my-integrations) → 새 Integration 생성 → Internal Token 복사
- **Notion Database ID**: 데이터베이스 URL에서 추출 (`https://www.notion.so/{database_id}?v=...`)

> Notion Integration을 해당 데이터베이스에 **Share (공유)** 해야 합니다.

### 4-A. 데스크톱 GUI 실행

```bash
python main.py
```

앱 실행 후 ⚙️ 설정 버튼에서 API 키를 입력합니다.

### 4-B. 텔레그램 봇 실행

**봇 생성:**
1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색
2. `/newbot` 명령으로 봇 생성
3. 발급된 토큰을 `config.json`의 `telegram_bot_token`에 입력

```bash
python telegram_bot.py
```

**API 키 설정 (두 가지 방법):**

텔레그램 채팅에서 직접 설정할 수 있습니다:

```
# 방법 1: 대화형 위자드 — /setup 만 입력하면 단계별 안내
/setup

# 방법 2: 직접 입력
/setup gemini <API 키>
/setup notion <Integration Token>
/setup database <Database ID>
```

또는 `config.json`을 직접 편집해도 됩니다.

**사용자 제한 (선택):**
- `config.json`의 `telegram_allowed_users`에 본인 chat_id를 추가하면 허가된 사용자만 사용 가능
- chat_id 확인: 봇에 아무 메시지 전송 후 `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 `chat.id` 확인
- 비워두면 모든 사용자 허용

**텔레그램 봇 명령어:**

| 명령어 | 기능 |
|--------|------|
| `/start` | 봇 소개 및 사용법 |
| `/setup` | API 키 설정 (위자드 또는 직접 입력) |
| `/status` | Gemini/Notion 연결 상태 확인 |
| `/help` | 상세 도움말 |
| `/cancel` | 진행 중인 설정 취소 |
| 텍스트 메시지 | 메모 입력 → AI 분석 → 미리보기 → 저장 |

---

## 설정 파일

`config.json`은 첫 실행 시 자동 생성됩니다.

```json
{
  "gemini_api_key": "your-gemini-api-key",
  "notion_token": "your-notion-integration-token",
  "notion_database_id": "your-database-id",
  "always_on_top": true,
  "telegram_bot_token": "your-telegram-bot-token",
  "telegram_allowed_users": []
}
```

| 필드 | 설명 | 필수 |
|------|------|------|
| `gemini_api_key` | Google Gemini API 키 | O |
| `notion_token` | Notion Internal Integration Token | O |
| `notion_database_id` | 대상 Notion 데이터베이스 ID | O |
| `always_on_top` | 데스크톱 GUI 항상 위 표시 | - |
| `telegram_bot_token` | 텔레그램 봇 토큰 (봇 사용 시) | 봇 사용 시 |
| `telegram_allowed_users` | 허용할 텔레그램 chat_id 목록 (빈 배열 = 전체 허용) | - |

---

## 사용 예시

### 입력 (메모)

```
삼성전자 재고실사 담당자 다시 연락함. 3/5까지 연장 요청했음.
전년도 동일 이슈 재발이라 감사 리스크 있을 수 있어서 선제적으로 보고함.
LG전자 계약 검토 건은 법무팀 회신 기다리는 중.
```

### AI 분석 결과

```
📋 분석 결과 (2개 태스크)

1. 📌 삼성전자 재고실사 진행 업데이트
   🏢 삼성전자  ⚡ High  📅 2026-03-05
   📎 → 기존: "삼성전자 재고실사"
   🏷 #재고실사 #삼성전자 #감사리스크
   📝 담당자 재연락, 3/5 연장 요청, 선제 보고
   💭 전년도 동일 이슈 재발이라 감사 리스크 있을 수 있어서 선제적으로 보고
   🚨 전년도 동일 이슈 재발, 감사 리스크

2. 📌 LG전자 계약 검토 법무팀 회신 대기
   🏢 LG전자  ⚡ Medium
   📄 새 페이지 생성
   🏷 #계약검토 #LG전자 #법무팀
   📝 법무팀 회신 대기 중
```

### Notion에 저장되는 구조

**신규 태스크 페이지:**
```
[페이지 본문]
┌─────────────────────────────────────────────────┐
│ 📋 메타데이터                                     │
│ 🏢 LG전자 | ⚡ Medium | 🏷 계약검토, LG전자, 법무팀 │
├─────────────────────────────────────────────────┤
│ ── 2026-02-28 14:30 ──                           │
│ 법무팀 회신 대기 중                                │
└─────────────────────────────────────────────────┘
```

**기존 태스크에 추가 메모:**
```
[기존 페이지 하단에 추가]
┌─────────────────────────────────────────────────┐
│ ─────────── (구분선) ───────────                  │
│ ── 📝 추가 메모 · 2026-02-28 14:30 ──            │
│ 담당자 재연락, 3/5 연장 요청, 선제 보고             │
│ 💭 전년도 동일 이슈 재발이라 감사 리스크 있을 수     │
│    있어서 선제적으로 보고                           │
│ 🚨 전년도 동일 이슈 재발, 감사 리스크               │
└─────────────────────────────────────────────────┘
```

---

## 데이터 활용

### Notion 내 활용
- **필터**: Tags, Client, Priority, Due Date 기준으로 뷰 생성
- **관계**: Related Tasks relation으로 연관 태스크 탐색
- **타임라인**: 태스크 페이지 내 시간순 업데이트 히스토리 추적
- **검색**: 판단 근거, 컨텍스트 요약 등 본문 텍스트 검색

### Markdown/CSV 추출
Notion은 데이터베이스를 **Markdown & CSV** 형태로 내보내기를 지원합니다:
- Notion 데이터베이스 → `...` 메뉴 → Export → Markdown & CSV
- 각 페이지가 개별 `.md` 파일로 추출됨 (프론트매터 형태의 속성 포함)
- CSV로 전체 속성을 테이블 형태로 추출 가능

추출된 Markdown 파일은 Obsidian, Logseq 등의 로컬 지식 관리 도구에서 바로 활용할 수 있고, CSV는 스프레드시트나 데이터 분석 도구에서 활용 가능합니다.

---

## 보안

이 도구는 **개인화된 1인용 도구**로 설계되었습니다. API 키 보호를 위한 보안 조치:

- **config.json은 .gitignore에 포함** — API 키가 Git에 커밋되지 않음
- **텔레그램 /setup 시 메시지 자동 삭제** — API 키가 포함된 메시지를 봇이 즉시 삭제. 삭제 실패 시 경고 메시지 표시
- **에러 메시지 sanitization** — API 호출 실패 시 상세 에러(키, URL 등)는 서버 로그에만 기록. 텔레그램에는 일반 안내만 표시
- **사용자 접근 제어** — `telegram_allowed_users`로 허가된 chat_id만 봇 사용 가능 (모든 핸들러에 적용)
- **ConversationHandler 상태 격리** — 사용자별로 독립된 대화 상태. 다른 사용자의 설정/메모와 간섭 없음

> `telegram_allowed_users`를 비워두면 봇 토큰을 아는 누구나 사용할 수 있습니다. 보안이 중요하면 반드시 본인 chat_id를 추가하세요.

---

## 아키텍처

```
┌───────────────────────────────────────────────────┐
│                    입력 채널                        │
│   ┌─────────────┐         ┌──────────────────┐    │
│   │ Desktop GUI │         │  Telegram Bot    │    │
│   │  (main.py)  │         │(telegram_bot.py) │    │
│   └──────┬──────┘         └────────┬─────────┘    │
│          └────────────┬────────────┘               │
│                       ▼                            │
│            ┌─────────────────────┐                 │
│            │   ConfigManager     │                 │
│            │   (config.json)     │                 │
│            └─────────────────────┘                 │
│                       │                            │
│          ┌────────────┼────────────┐               │
│          ▼                         ▼               │
│  ┌───────────────┐       ┌─────────────────┐      │
│  │  GeminiClient │       │  NotionClient   │      │
│  │  (Gemini API) │       │  (Notion API)   │      │
│  └───────────────┘       └─────────────────┘      │
│                                    │               │
│                                    ▼               │
│                          ┌─────────────────┐       │
│                          │ Notion Database │       │
│                          │  (구조화 데이터)  │       │
│                          └─────────────────┘       │
└───────────────────────────────────────────────────┘
```

**핵심 클래스:**

| 클래스 | 역할 |
|--------|------|
| `ConfigManager` | `config.json` 로드/저장, 기본값 병합 |
| `GeminiClient` | Gemini API 호출, 메모 분석, 컨텍스트 인식 프롬프트 |
| `NotionClient` | Notion API 호출, 페이지 생성/추가, 속성 관리, 스키마 감지 |
| `NoteAssistantApp` | 데스크톱 GUI (Tkinter), 미리보기 다이얼로그, 메모 히스토리 |

`GeminiClient`와 `NotionClient`는 GUI 의존성 없이 독립적으로 동작하므로, 텔레그램 봇이나 향후 다른 입력 채널에서 그대로 재사용됩니다.

---

## 프로젝트 구조

```
.
├── main.py             # 데스크톱 GUI + 핵심 비즈니스 로직 (ConfigManager, GeminiClient, NotionClient)
├── telegram_bot.py     # 텔레그램 봇 입력 채널
├── config.json         # 설정 파일 (자동 생성, .gitignore 권장)
├── requirements.txt    # Python 의존성
└── README.md
```

## 단축키 (데스크톱)

| 단축키 | 기능 |
|--------|------|
| `Ctrl+Enter` (Windows/Linux) | 저장 |
| `Cmd+Enter` (macOS) | 저장 |

## 기술 스택

- **Python 3.10+**
- **Tkinter** — 크로스 플랫폼 데스크톱 GUI
- **python-telegram-bot** — 텔레그램 봇 프레임워크
- **Google Gemini API** (`google-genai`) — AI 메모 분석
- **Notion API** (`requests`) — 데이터베이스 연동

## 라이선스

MIT License

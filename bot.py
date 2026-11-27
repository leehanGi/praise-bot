import os
import re
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
import gspread
from google.oauth2.service_account import Credentials

# 로깅 설정
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 1. 텔레그램 봇 토큰 및 QR 접근 코드
TOKEN = os.environ.get("BOT_TOKEN")
QR_ACCESS_CODE = "praise2026qr"  # 테스트 중 일반 /start로 바로 열고 싶으면 "" 로 비워두세요

# 2. 구글 인증 정보 및 시트 연동 설정
try:
    cred_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if cred_json_str:
        with open("credentials.json", "w", encoding="utf-8") as f:
            f.write(cred_json_str)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    client = gspread.authorize(creds)

    # 구글 시트 이름 연결
    spreadsheet = client.open("찬양부 사진전 접수")
    sheet = spreadsheet.sheet1
except Exception as e:
    print(f"🚨 구글 시트 연동 또는 인증 파일 에러 발생: {e}")

# 대화 단계 정의 (팀 선택 -> 기타 세부팀 -> 이름 -> 사진 번호 -> 이유)
TEAM, OTHER_TEAM, NAME, PHOTO_NUM, REASON = range(5)

# 선택 가능한 팀 목록 및 '기타' 세부 팀 목록
TEAM_CHOICES = [["주일찬양대", "관현악"], ["수정찬", "수저찬"], ["찬양단", "기타"]]
VALID_TEAMS = {team for row in TEAM_CHOICES for team in row}

OTHER_TEAM_CHOICES = [["작곡팀", "건반팀"], ["운영진", "⬅️ 뒤로가기"]]
VALID_OTHER_TEAMS = {"작곡팀", "건반팀", "운영진"}

BACK_BTN = "⬅️ 뒤로가기"
BACK_KEYBOARD = ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)

# 각 팀 블록의 시작 열 번호 (1=A, 6=F, 11=K, 16=P, 21=U, 26=Z)
# 블록 구성: [시작열: 팀, +1: 이름, +2: 사진번호, +3: 사진 고른이유], 이후 1열 공백
TEAM_START_COL = {
    "주일찬양대": 1,   # A~D열 (E열 공백)
    "관현악": 6,       # F~I열 (J열 공백)
    "수정찬": 11,      # K~N열 (O열 공백)
    "수저찬": 16,      # P~S열 (T열 공백)
    "찬양단": 21,      # U~X열 (Y열 공백)
    "기타": 26,        # Z~AC열 (시트 열 Z~AC 이상 확보 필요)
}

def col_to_letter(col_num: int) -> str:
    """1 -> 'A', 26 -> 'Z', 27 -> 'AA' 변환"""
    result = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        result = chr(65 + remainder) + result
    return result

def append_team_submission(ws, team: str, name: str, photo_list: list, reason: str):
    """
    해당 팀 블록(4개 열: 팀, 이름, 사진번호, 사진 고른이유)에서 마지막 행을 찾은 뒤,
    - 첫 행: [팀, 이름, 첫 사진번호, 고른이유] 기록 + 4개 칸 전체 연한 노란색 강조
    - 이후 사진 행: [팀, 이름, 다음 사진번호, ""] 형태로 세로 추가
    """
    start_col = TEAM_START_COL["기타"] if team.startswith("기타") else TEAM_START_COL.get(team)
    if not start_col:
        raise ValueError(f"등록되지 않은 팀입니다: {team}")

    block_cols = [ws.col_values(c) for c in range(start_col, start_col + 4)]
    max_len = max((len(col) for col in block_cols), default=1)
    next_row = max(max_len + 1, 2)

    first_photo = photo_list[0]
    rest_photos = photo_list[1:]

    start_letter = col_to_letter(start_col)
    end_letter = col_to_letter(start_col + 3)
    first_row_range = f"{start_letter}{next_row}:{end_letter}{next_row}"

    # 1. 첫 번째 사진 행: 팀, 이름, 첫 사진번호, 고른이유 기록
    ws.update(
        range_name=first_row_range,
        values=[[team, name, first_photo, reason]],
        value_input_option="USER_ENTERED"
    )

    # 첫 번째 행 4개 필드(팀, 이름, 사진번호, 고른이유) 전체 노란색 배경 적용
    ws.format(
        first_row_range,
        {
            "backgroundColor": {
                "red": 1.0,
                "green": 0.96,
                "blue": 0.6
            }
        }
    )

    # 2. 두 번째 사진 번호부터: 팀, 이름은 반복 기록하고 고른이유는 비움
    if rest_photos:
        extra_rows = [[team, name, p, ""] for p in rest_photos]
        end_row = next_row + len(rest_photos)
        ws.update(
            range_name=f"{start_letter}{next_row + 1}:{end_letter}{end_row}",
            values=extra_rows,
            value_input_option="USER_ENTERED"
        )

# 사진 번호 문자열을 분리해 리스트로 반환
# 지원 예시:
# - 일반 나열: "1, 2, 3", "1번 2번 3번"
# - 연속 범위: "1-10", "1~10", "1번부터 10번"
# - 동일 번호 수량: "100*10", "100x10", "100번 10장", "100 10장", "100(10장)"
def parse_photo_numbers(raw_text: str):
    text = raw_text.strip()

    # '1번부터 10번' 또는 '1부터 10' 형태를 '1-10'으로 미리 변환
    text = re.sub(r"(\d+)\s*번?\s*부터\s*(\d+)\s*번?", r"\1-\2", text)

    # '100번 10장', '100 10장', '100(10장)', '100-10장' 형태를 '100*10'으로 미리 변환
    text = re.sub(r"(\d+)\s*(?:번|\(|-|\s)\s*(\d+)\s*장\)?", r"\1*\2", text)

    # 뒤에 남은 단독 '번' 제거 (예: "1번, 2번" -> "1, 2")
    text = re.sub(r"(\d+)\s*번", r"\1", text)

    parts = [p.strip() for p in re.split(r"[,\s/]+", text) if p.strip()]
    parsed = []

    for p in parts:
        # 1) 동일 번호 반복 ('100*10', '100x10')
        repeat_match = re.fullmatch(r"(\d+)\s*[*xX×]\s*(\d+)", p)
        if repeat_match:
            photo_num = int(repeat_match.group(1))
            count = int(repeat_match.group(2))
            if count > 0:
                parsed.extend([photo_num] * count)
            continue

        # 2) 연속 범위 ('1-10', '1~10')
        range_match = re.fullmatch(r"(\d+)\s*[-~～]\s*(\d+)", p)
        if range_match:
            start_num = int(range_match.group(1))
            end_num = int(range_match.group(2))
            if start_num <= end_num:
                parsed.extend(range(start_num, end_num + 1))
            else:
                parsed.extend(range(start_num, end_num - 1, -1))
        elif p.isdigit():
            parsed.append(int(p))
        else:
            parsed.append(p)

    return parsed if parsed else [raw_text.strip()]

# --- 단계별 질문 함수 (뒤로가기 시 재사용) ---

async def ask_team(update: Update):
    reply_markup = ReplyKeyboardMarkup(TEAM_CHOICES, resize_keyboard=True)
    await update.message.reply_text(
        "안녕하세요! **2026 찬양부 사진전 접수 봇**입니다. 📸\n\n"
        "안내에 따라 순서대로 입력하시면 접수가 완료됩니다.\n"
        "*(입력 도중 수정이 필요하면 하단의 **`⬅️ 뒤로가기`** 버튼을 눌러주세요.)*\n\n"
        "먼저 아래에서 소속 **[팀]**을 선택해 주세요!",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return TEAM

async def ask_other_team(update: Update):
    reply_markup = ReplyKeyboardMarkup(OTHER_TEAM_CHOICES, resize_keyboard=True)
    await update.message.reply_text(
        "선택하신 팀: *기타*\n\n"
        "구체적인 팀을 아래 버튼에서 선택해 주세요!",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return OTHER_TEAM

async def ask_name(update: Update, team: str):
    await update.message.reply_text(
        f"선택하신 팀: *{team}*\n\n"
        "신청하시는 분의 **[성함]**을 입력해 주세요.",
        reply_markup=BACK_KEYBOARD,
        parse_mode="Markdown"
    )
    return NAME

async def ask_photo_num(update: Update):
    await update.message.reply_text(
        "신청하실 **[사진 번호]**를 입력해 주세요.\n\n"
        "• 여러 장 고를 때: `1, 2, 3` 또는 `1번부터 10번`\n"
        "• 같은 사진 여러 장: `100번 10장` (또는 `100*10`)",
        reply_markup=BACK_KEYBOARD,
        parse_mode="Markdown"
    )
    return PHOTO_NUM

# --- 핸들러 함수 ---

# /start 명령어: QR 파라미터 검증 후 첫 번째 단계(팀 선택)로 진입
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if QR_ACCESS_CODE:
        args = context.args or []
        if not args or args[0] != QR_ACCESS_CODE:
            await update.message.reply_text(
                "현장에 비치된 전용 QR 코드를 스캔해 접속해 주세요.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

    context.user_data.clear()
    return await ask_team(update)

# 1단계: 팀 저장 후 -> '기타'면 세부 팀 버튼 표시, 아니면 바로 이름 묻기
async def select_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = update.message.text.strip()
    if team not in VALID_TEAMS:
        reply_markup = ReplyKeyboardMarkup(TEAM_CHOICES, resize_keyboard=True)
        await update.message.reply_text(
            "하단 버튼에서 소속 **[팀]**을 선택해 주세요!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return TEAM

    context.user_data['base_team'] = team
    if team == "기타":
        return await ask_other_team(update)

    context.user_data['team'] = team
    return await ask_name(update, team)

# 1-1단계: '기타' 세부 팀 선택 후 -> 이름 묻기
async def select_other_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == BACK_BTN:
        return await ask_team(update)

    if text not in VALID_OTHER_TEAMS:
        reply_markup = ReplyKeyboardMarkup(OTHER_TEAM_CHOICES, resize_keyboard=True)
        await update.message.reply_text(
            "하단 버튼에서 *작곡팀*, *건반팀*, *운영진* 중 하나를 선택해 주세요!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return OTHER_TEAM

    formatted_team = f"기타 ({text})"
    context.user_data['team'] = formatted_team
    return await ask_name(update, formatted_team)

# 2단계: 이름 저장 후 -> 사진 번호 묻기
async def input_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == BACK_BTN:
        if context.user_data.get('base_team') == "기타":
            return await ask_other_team(update)
        return await ask_team(update)

    context.user_data['name'] = text
    return await ask_photo_num(update)

# 3단계: 사진 번호 저장 후 -> 고른 이유 묻기
async def input_photo_num(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == BACK_BTN:
        return await ask_name(update, context.user_data.get('team', ''))

    context.user_data['photo_num'] = text
    await update.message.reply_text(
        "마지막으로, 이 **[사진을 고른 이유]**나 작품에 담긴 사연을 간단히 적어주세요.",
        reply_markup=BACK_KEYBOARD,
        parse_mode="Markdown"
    )
    return REASON

# 4단계: 이유 저장 후 -> 팀별 열 위치에 맞춰 구글 시트 저장 및 완료 메시지
async def input_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    if reason == BACK_BTN:
        return await ask_photo_num(update)

    user_data = context.user_data
    team = user_data.get('team')
    name = user_data.get('name')
    raw_photo_num = user_data.get('photo_num', "")
    photo_list = parse_photo_numbers(raw_photo_num)

    try:
        append_team_submission(sheet, team, name, photo_list, reason)
    except Exception as e:
        logging.exception("구글 시트 데이터 저장 중 에러 발생")
        await update.message.reply_text(
            f"⚠️ 시트 저장 중 문제가 발생했습니다.\n원인: `{e}`\n관리자에게 문의해 주세요.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"🎉 찬양부 사진전 접수가 완벽하게 완료되었습니다! (총 {len(photo_list)}건 반영)\n"
        "소중한 참여 감사합니다. 🙏",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

# 취소 명령어
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "접수가 취소되었습니다. 다시 시작하려면 현장 QR 코드를 다시 스캔해 주세요.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

if __name__ == '__main__':
    try:
        if not TOKEN:
            raise ValueError("BOT_TOKEN 환경 변수가 설정되지 않았습니다.")

        application = ApplicationBuilder().token(TOKEN).build()

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                TEAM: [MessageHandler(filters.TEXT & (~filters.COMMAND), select_team)],
                OTHER_TEAM: [MessageHandler(filters.TEXT & (~filters.COMMAND), select_other_team)],
                NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), input_name)],
                PHOTO_NUM: [MessageHandler(filters.TEXT & (~filters.COMMAND), input_photo_num)],
                REASON: [MessageHandler(filters.TEXT & (~filters.COMMAND), input_reason)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel),
                CommandHandler('start', start),
            ],
        )

        application.add_handler(conv_handler)

        print("단계별 접수 봇이 성공적으로 실행되었습니다...")
        application.run_polling()
    except Exception as e:
        print("🚨 봇 실행 중 치명적인 에러 발생:", e)
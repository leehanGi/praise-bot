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

# 대화 단계 정의 (팀 선택 -> 이름 -> 사진 번호 -> 이유)
TEAM, NAME, PHOTO_NUM, REASON = range(4)

# 선택 가능한 팀 목록 및 뒤로가기 버튼 설정
TEAM_CHOICES = [["주일찬양대", "관현악"], ["수정찬", "수저찬"], ["찬양단", "기타"]]
VALID_TEAMS = {team for row in TEAM_CHOICES for team in row}
BACK_BTN = "⬅️ 뒤로가기"
BACK_KEYBOARD = ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)

# 사진 번호 문자열을 분리해 리스트로 반환 (예: "1,2,3,4", "1 2 3 4", "1/2/3/4")
def parse_photo_numbers(raw_text: str):
    parts = [p.strip() for p in re.split(r"[/,\s]+", raw_text.strip()) if p.strip()]
    parsed = []
    for p in parts:
        parsed.append(int(p) if p.isdigit() else p)
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
        "신청하실 **[사진 번호]**를 입력해 주세요.\n"
        "예시) `1,2,3,4` 또는 `1 2 3 4`",
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
                "현장에 비치된 전용 QR 코드를 통해 접속하고 재접속시 다시 코드를 스캔 해주세요.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

    context.user_data.clear()
    return await ask_team(update)

# 1단계: 팀 저장 후 -> 이름 묻기
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

    context.user_data['team'] = team
    return await ask_name(update, team)

# 2단계: 이름 저장 후 -> 사진 번호 묻기
async def input_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == BACK_BTN:
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

# 4단계: 이유 저장 후 -> 구글 시트 최종 저장(사진 번호당 1행) 및 완료 메시지
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
        # 사진 번호 1개당 1행씩 세로로 추가 [팀, 이름, 사진번호, 고른 이유]
        rows = [[team, name, p_num, reason] for p_num in photo_list]
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"🚨 구글 시트 데이터 저장 중 에러 발생: {e}")

    await update.message.reply_text(
        f"🎉 찬양부 사진전 접수가 완벽하게 완료되었습니다! (총 {len(photo_list)}건 반영)\n"
        "소중한 참여 감사합니다. 🙏",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

# 취소 명령어
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("접수가 취소되었습니다. 다시 시작하려면 /start를 눌러주세요.", reply_markup=ReplyKeyboardRemove())
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
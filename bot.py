import os
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

# 1. 텔레그램 봇 토큰 (Railway Variables의 BOT_TOKEN에서 불러옴)
TOKEN = os.environ.get("BOT_TOKEN")

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

# 선택 가능한 팀 목록
TEAM_CHOICES = [["주일찬양대", "관현악"], ["수정찬", "수저찬"], ["찬양단", ""]]

# /start 명령어: 첫 번째 단계(팀 선택)로 진입
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup(TEAM_CHOICES, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        "안녕하세요! 2026 찬양부 사진전 접수 봇입니다. 📸\n"
        "먼저 소속 **[팀]**을 선택해 주세요.",
        reply_markup=reply_markup
    )
    return TEAM

# 1단계: 팀 저장 후 -> 이름 묻기
async def select_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['team'] = update.message.text
    await update.message.reply_text(
        f"선택하신 팀: {update.message.text}\n\n"
        "신청하시는 분의 **[성함]**을 입력해 주세요.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return NAME

# 2단계: 이름 저장 후 -> 사진 번호 묻기
async def input_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text(
        "신청하실 **[사진 번호]**를 입력해 주세요. ex) 1/2/3/4"
    )
    return PHOTO_NUM

# 3단계: 사진 번호 저장 후 -> 고른 이유 묻기
async def input_photo_num(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['photo_num'] = update.message.text
    await update.message.reply_text(
        "마지막으로, 이 **[사진을 고른 이유]**나 작품에 담긴 사연을 간단히 적어주세요."
    )
    return REASON

# 4단계: 이유 저장 후 -> 구글 시트 최종 저장 및 완료 메시지
async def input_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    user_data = context.user_data
    
    team = user_data.get('team')
    name = user_data.get('name')
    photo_num = user_data.get('photo_num')
    
    try:
        # 구글 시트에 행 추가 [팀, 이름, 사진번호, 고른 이유]
        sheet.append_row([team, name, photo_num, reason])
    except Exception as e:
        print(f"🚨 구글 시트 데이터 저장 중 에러 발생: {e}")
    
    await update.message.reply_text(
        "🎉 찬양부 사진전 접수가 완벽하게 완료되었습니다!\n"
        "소중한 참여 감사합니다. 🙏"
    )
    
    return ConversationHandler.END

# 취소 명령어
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            fallbacks=[CommandHandler('cancel', cancel)],
        )
        
        application.add_handler(conv_handler)
        
        print("단계별 접수 봇이 성공적으로 실행되었습니다...")
        application.run_polling()
    except Exception as e:
        print("🚨 봇 실행 중 치명적인 에러 발생:", e)
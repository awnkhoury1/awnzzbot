import asyncio
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, Audio
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# Read bot token from environment variable (set in Heroku/Railway/etc.)
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

# Database setup (PostgreSQL for online deployment)
DATABASE_URL = os.getenv('DATABASE_URL')  # Provided by Heroku/Railway

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlists (
            user_id BIGINT,
            playlist_name TEXT,
            song_title TEXT,
            song_url TEXT,
            PRIMARY KEY (user_id, playlist_name, song_url)
        )
    ''')
    conn.commit()
    conn.close()

# For local testing with SQLite (uncomment and comment out PostgreSQL above if needed)
# import sqlite3
# DB_FILE = 'playlists.db'
# def get_db_connection():
#     return sqlite3.connect(DB_FILE)
# def init_db():
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS playlists (
#             user_id INTEGER,
#             playlist_name TEXT,
#             song_title TEXT,
#             song_url TEXT,
#             PRIMARY KEY (user_id, playlist_name, song_url)
#         )
#     ''')
#     conn.commit()
#     conn.close()

async def download_audio(url_or_query, user_id):
    """Download audio from YouTube URL or search query."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'/tmp/{user_id}_%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    
    if not url_or_query.startswith('http'):
        # Search for the song if it's a name
        ydl_opts['default_search'] = 'ytsearch1:'
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url_or_query, download=True)
            title = info.get('title', 'Unknown')
            filename = ydl.prepare_filename(info).replace('.webm', '.mp3').replace('.m4a', '.mp3')
            return filename, title
        except Exception as e:
            return None, str(e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if not text:
        await update.message.reply_text("Please send a YouTube link or song name.")
        return
    
    await update.message.reply_text("Downloading... Please wait.")
    
    filename, title = await download_audio(text, user_id)
    
    if filename and os.path.exists(filename):
        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(Audio(audio_file, title=title, filename=f"{title}.mp3"))
        os.remove(filename)  # Clean up
    else:
        await update.message.reply_text(f"Failed to download: {title}")

async def create_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /create_playlist <name>")
        return
    playlist_name = ' '.join(context.args)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO playlists (user_id, playlist_name, song_title, song_url) VALUES (%s, %s, %s, %s)', (user_id, playlist_name, '', ''))
        conn.commit()
        await update.message.reply_text(f"Playlist '{playlist_name}' created.")
    except psycopg2.IntegrityError:
        await update.message.reply_text(f"Playlist '{playlist_name}' already exists.")
    conn.close()

async def add_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_to_playlist <playlist_name> <song_link_or_name>")
        return
    playlist_name = context.args[0]
    song_input = ' '.join(context.args[1:])
    
    # Download to get title and URL
    filename, error = await download_audio(song_input, user_id)
    if not filename:
        await update.message.reply_text(f"Failed to add: {error}")
        return
    
    # Extract URL from yt-dlp (simplified; in practice, parse from info)
    # For simplicity, assume song_input is URL; enhance if needed
    song_url = song_input if song_input.startswith('http') else f"https://www.youtube.com/watch?v={song_input}"  # Placeholder
    song_title = filename.split('/')[-1].replace('.mp3', '').replace(f'{user_id}_', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO playlists (user_id, playlist_name, song_title, song_url) VALUES (%s, %s, %s, %s)', (user_id, playlist_name, song_title, song_url))
    conn.commit()
    conn.close()
    os.remove(filename)  # Clean up
    await update.message.reply_text(f"Added '{song_title}' to '{playlist_name}'.")

async def view_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /view_playlist <name>")
        return
    playlist_name = ' '.join(context.args)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT song_title, song_url FROM playlists WHERE user_id = %s AND playlist_name = %s AND song_title != %s', (user_id, playlist_name, ''))
    songs = cursor.fetchall()
    conn.close()
    if not songs:
        await update.message.reply_text(f"No songs in '{playlist_name}'.")
        return
    response = f"Playlist '{playlist_name}':\n" + '\n'.join([f"- {song['song_title']} ({song['song_url']})" for song in songs])
    await update.message.reply_text(response)

async def delete_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /delete_playlist <name>")
        return
    playlist_name = ' '.join(context.args)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM playlists WHERE user_id = %s AND playlist_name = %s', (user_id, playlist_name))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Playlist '{playlist_name}' deleted.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Send a YouTube link or song name to download audio.\n"
        "Commands:\n"
        "/create_playlist <name>\n"
        "/add_to_playlist <playlist_name> <song_link_or_name>\n"
        "/view_playlist <name>\n"
        "/delete_playlist <name>"
    )

def main():
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("create_playlist", create_playlist))
    application.add_handler(CommandHandler("add_to_playlist", add_to_playlist))
    application.add_handler(CommandHandler("view_playlist", view_playlist))
    application.add_handler(CommandHandler("delete_playlist", delete_playlist))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.run_polling()

if __name__ == '__main__':
    main()
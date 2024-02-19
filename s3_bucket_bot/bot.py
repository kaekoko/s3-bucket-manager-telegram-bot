import os
import html
import json
import logging
import traceback
import uuid
from os import path
import mimetypes
import requests
from requests.exceptions import HTTPError

from telegram import Update, ParseMode, File
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, Defaults
from telegram import PhotoSize, Audio, Animation, Video, Document

from .s3bucket import upload_file as s3_upload_file, get_obj_url as s3_get_obj_url, delete_file as s3_delete_file, \
    make_public as s3_make_public, make_private as s3_make_private, file_exist as s3_file_exist, \
    copy_file as s3_copy_file, get_file_acl as s3_get_file_acl, list_files as s3_list_files, \
    get_meta as s3_get_meta

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# The token you got from @botfather when you created the bot
TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
TELEGRAM_USERNAME = os.getenv('TELEGRAM_USERNAME')

# This can be your own ID, or one for a developer group/channel.
# You can use the /start command of this bot to see your chat id.
DEVELOPER_CHAT_ID = os.getenv('DEVELOPER_CHAT_ID')

TEMP_PATH = os.getenv('TEMP_PATH', '/tmp')

DIGITALOCEAN_TOKEN = os.getenv('DIGITALOCEAN_TOKEN')
BUCKET_NAME = None
if os.getenv('BUCKET_NAME', '').strip():
    BUCKET_NAME = os.getenv('BUCKET_NAME')
ENDPOINT_URL = None
if os.getenv('ENDPOINT_URL', '').strip():
    ENDPOINT_URL = os.getenv('ENDPOINT_URL')


# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def start(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    if update.effective_message.from_user.username != TELEGRAM_USERNAME:
        update.effective_message.reply_html(
            f'<b>Access denied</b>\n\n'
            f'Your chat id is <code>{update.effective_chat.id}</code>.\n'
            f'Your username is <code>{update.effective_message.from_user.username}</code>.'
        )
    else:
        update.message.reply_text("My dear cruel world do you ever think about me?")


def help_command(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /help is issued."""
    update.message.reply_text("My dear cruel world do you ever think about me?")


def echo(update: Update, context: CallbackContext) -> None:
    """Echo the user message."""
    update.message.reply_text(update.message.text)


def bad_command(update: Update, context: CallbackContext) -> None:
    """Raise an error to trigger the error handler."""
    raise Exception("Something went wrong, please try again later.")


def upload_file(update: Update, context: CallbackContext) -> None:
    attachment = update.message.effective_attachment
    if isinstance(attachment, list):
        attachment = attachment[-1]

       

    # @see https://core.telegram.org/bots/api#getfile
    if attachment.file_size > 20 * 1024 * 1024:
        
        update.message.reply_html(
            f'<b>File is too big</b>\n\n'
            f'For the moment, <a href="https://core.telegram.org/bots/api#getfile">bots can download files of up to 20MB in size</a>.\n'
        )
        return

    file = attachment.get_file()

    def get_original_file_name():
        original_file_name = path.basename(file.file_path)
        if hasattr(attachment, 'file_name'):
            original_file_name = attachment.file_name
        return original_file_name

    file_name = get_original_file_name()
    if update.message.caption is not None:
        if update.message.caption.strip():
            # Trim spaces and remove leading slash
            file_name = update.message.caption.strip().lstrip('/')
            if file_name.endswith('/'):
                file_name += get_original_file_name()

    mime_type = mimetypes.MimeTypes().guess_type(file_name)[0]
    if hasattr(attachment, 'mime_type'):
        mime_type = attachment.mime_type

    tmp_file_name = f'{TEMP_PATH}/{uuid.uuid4()}'
    file = File.download(file, tmp_file_name)
    s3_upload_file(file, file_name, mime_type, 'public-read')  # Make public by default
    try:
        os.unlink(tmp_file_name)
    except Exception as e:
        logger.error(e)
    s3_file_path = s3_get_obj_url(file_name)
    update.message.reply_text(text=s3_file_path)


def delete_file(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_delete_file(file_name)
        update.message.reply_text(
            text=f'File {s3_file_path} has been deleted. Do not forget to clear all of your edge caches.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def make_public(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_make_public(file_name)
        update.message.reply_text(text=f'File {s3_file_path} has become public.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def make_private(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        s3_make_private(file_name)
        update.message.reply_text(text=f'File {s3_file_path} has become private.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def file_exist(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        if s3_file_exist(file_name):
            update.message.reply_text(text=f'File {s3_file_path} exist.')
            return
        update.message.reply_text(text=f'File {s3_file_path} does not exist.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def copy_file(update: Update, context: CallbackContext):
    if len(context.args) < 2:
        return

    src = context.args[0].strip().lstrip('/')
    dest = context.args[1].strip().lstrip('/')
    try:
        s3_src_path = s3_get_obj_url(src)
        if not s3_file_exist(src):
            update.message.reply_text(text=f'Source file {s3_src_path} does not exist.')
            return

        s3_dest_path = s3_get_obj_url(dest)
        s3_copy_file(src, dest)
        update.message.reply_text(text=f'File {s3_src_path} has been copied to {s3_dest_path}.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def get_file_acl(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        acl = s3_get_file_acl(file_name)
        update.message.reply_text(text=f'File {s3_file_path} is {acl}.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def list_files(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    prefix = context.args[0].strip().lstrip('/')
    limit = 10
    if len(context.args) == 2:
        limit = int(context.args[1])
    entries = s3_list_files(prefix, limit=limit)
    if len(entries) == 0:
        update.message.reply_text(text='Not found')
        return

    message = '\n'.join(list(map(lambda entry: s3_get_obj_url(entry['key']), entries)))
    update.message.reply_text(text=message)


def get_metadata(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    file_name = context.args[0].strip().lstrip('/')
    try:
        response = s3_get_meta(file_name)
        logger.info(response)
        update.message.reply_text(text=f'{response}')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def purge_cache(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        return

    if DIGITALOCEAN_TOKEN is None:
        raise Exception('Service is not available.')

    file_name = context.args[0].strip().lstrip('/')
    try:
        s3_file_path = s3_get_obj_url(file_name)
        endpoint_url = ENDPOINT_URL.lstrip('https://')
        origin = f'{BUCKET_NAME}.{endpoint_url}'
        headers = {
            'Authorization': f'Bearer {DIGITALOCEAN_TOKEN}',
            'Content-Type': 'application/json',
        }
        api_url = f'https://api.digitalocean.com/v2/cdn/endpoints'
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if 'endpoints' not in data:
            raise Exception('No endpoints found.')

        endpoints = list(filter(lambda endpoint: endpoint['origin'] == origin, data['endpoints']))
        if len(endpoints) == 0:
            raise Exception('No endpoints found.')

        endpoint_id = endpoints[0]['id']
        logger.info(endpoint_id)

        api_url = f'https://api.digitalocean.com/v2/cdn/endpoints/{endpoint_id}/cache'
        response = requests.delete(api_url, headers=headers, json={
            'files': [file_name]
        })
        response.raise_for_status()
        update.message.reply_text(text=f'File {s3_file_path} has been cleared from all of your edge caches.')
    except Exception as e:
        logger.error(e)
        update.message.reply_text(text=f'Error: {e}')


def error_handler(update: Update, context: CallbackContext) -> None:
    """Log the error or/and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    message = (
        f'An exception was raised while handling an update\n'
        f'<pre>update = {html.escape(json.dumps(update.to_dict(), indent=2, ensure_ascii=False))}'
        '</pre>\n\n'
        f'<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n'
        f'<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n'
        f'<pre>{html.escape(tb_string)}</pre>'
    )

    chat_id = DEVELOPER_CHAT_ID
    if chat_id is None:
        # Send error message back to current chat.
        chat_id = update.effective_chat.id

    # Finally, send the message
    context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.HTML)


def main():
    """Start the bot."""
    # Create the Updater and pass it your bot's token.
    # Make sure to set use_context=True to use the new context based callbacks
    # Post version 12 this will no longer be necessary
    defaults = Defaults(disable_web_page_preview=True)
    updater = Updater(TELEGRAM_API_TOKEN, use_context=True, defaults=defaults)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # on different commands - answer in Telegram
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler('bad_command', bad_command, Filters.user(username=TELEGRAM_USERNAME)))

    # on noncommand i.e message - echo the message on Telegram
    dispatcher.add_handler(MessageHandler(Filters.text
                                          & Filters.user(username=TELEGRAM_USERNAME)
                                          & ~Filters.command, echo))

    # upload file to S3
    dispatcher.add_handler(MessageHandler((Filters.photo
                                           | Filters.attachment
                                           | Filters.audio
                                           | Filters.video
                                           | Filters.animation
                                           | Filters.document)
                                          & Filters.user(username=TELEGRAM_USERNAME)
                                          & ~Filters.command, upload_file))

    # delete file from s3 by path
    dispatcher.add_handler(CommandHandler('delete',
                                          delete_file,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # make file public
    dispatcher.add_handler(CommandHandler('make_public',
                                          make_public,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # make file private
    dispatcher.add_handler(CommandHandler('make_private',
                                          make_private,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # check if file exist
    dispatcher.add_handler(CommandHandler('exist',
                                          file_exist,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # Could be used to copy, move or rename file
    dispatcher.add_handler(CommandHandler('copy_file',
                                          copy_file,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # check file acl
    dispatcher.add_handler(CommandHandler('get_file_acl',
                                          get_file_acl,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # list bucket objects
    dispatcher.add_handler(CommandHandler('list',
                                          list_files,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # get object metadata
    dispatcher.add_handler(CommandHandler('get_meta',
                                          get_metadata,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # purge cache
    dispatcher.add_handler(CommandHandler('purge_cache',
                                          purge_cache,
                                          Filters.user(username=TELEGRAM_USERNAME),
                                          pass_args=True))

    # Register the error handler.
    dispatcher.add_error_handler(error_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()

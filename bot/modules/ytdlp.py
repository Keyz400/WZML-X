#!/usr/bin/env python3
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.filters import command, regex, user
from asyncio import sleep, wait_for, Event, wrap_future
from aiohttp import ClientSession
from aiofiles.os import path as aiopath
from yt_dlp import YoutubeDL
from functools import partial
from time import time

from bot import DOWNLOAD_DIR, bot, categories_dict, config_dict, user_data, LOGGER
from bot.helper.ext_utils.task_manager import task_utils
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, deleteMessage, auto_delete_message, delete_links, open_category_btns, open_dump_btns
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.bot_utils import get_readable_file_size, fetch_user_tds, fetch_user_dumps, is_url, is_gdrive_link, new_task, sync_to_async, new_task, is_rclone_path, new_thread, get_readable_time, arg_parser
from bot.helper.mirror_utils.download_utils.yt_dlp_download import YoutubeDLHelper
from bot.helper.mirror_utils.rclone_utils.list import RcloneList
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.listeners.tasks_listener import MirrorLeechListener
from bot.helper.ext_utils.help_messages import YT_HELP_MESSAGE
from bot.helper.ext_utils.bulk_links import extract_bulk_links


@new_task
async def select_format(_, query, obj):
    data = query.data.split()
    message = query.message
    await query.answer()

    if data[1] == 'dict':
        b_name = data[2]
        await obj.qual_subbuttons(b_name)
    elif data[1] == 'mp3':
        await obj.mp3_subbuttons()
    elif data[1] == 'audio':
        await obj.audio_format()
    elif data[1] == 'aq':
        if data[2] == 'back':
            await obj.audio_format()
        else:
            await obj.audio_quality(data[2])
    elif data[1] == 'back':
        await obj.back_to_main()
    elif data[1] == 'cancel':
        await editMessage(message, 'Task has been cancelled.')
        obj.qual = None
        obj.is_cancelled = True
        obj.event.set()
    else:
        if data[1] == 'sub':
            obj.qual = obj.formats[data[2]][data[3]][1]
        elif '|' in data[1]:
            obj.qual = obj.formats[data[1]]
        else:
            obj.qual = data[1]
        obj.event.set()


class YtSelection:
    def __init__(self, client, message):
        self.__message = message
        self.__user_id = message.from_user.id
        self.__client = client
        self.__is_m4a = False
        self.__reply_to = None
        self.__time = time()
        self.__timeout = 120
        self.__is_playlist = False
        self.is_cancelled = False
        self.__main_buttons = None
        self.event = Event()
        self.formats = {}
        self.qual = None

    @new_thread
    async def __event_handler(self):
        pfunc = partial(select_format, obj=self)
        handler = self.__client.add_handler(CallbackQueryHandler(
            pfunc, filters=regex('^ytq') & user(self.__user_id)), group=-1)
        try:
            await wait_for(self.event.wait(), timeout=self.__timeout)
        except Exception:
            await editMessage(self.__reply_to, 'Timed Out. Task has been cancelled!')
            self.qual = None
            self.is_cancelled = True
            self.event.set()
        finally:
            self.__client.remove_handler(*handler)

    async def get_quality(self, result):
        future = self.__event_handler()
        buttons = ButtonMaker()
        if 'entries' in result:
            self.__is_playlist = True
            for i in ['144', '240', '360', '480', '720', '1080', '1440', '2160']:
                video_format = f'bv*[height<=?{i}][ext=mp4]+ba[ext=m4a]/b[height<=?{i}]'
                b_data = f'{i}|mp4'
                self.formats[b_data] = video_format
                buttons.ibutton(f'{i}-mp4', f'ytq {b_data}')
                video_format = f'bv*[height<=?{i}][ext=webm]+ba/b[height<=?{i}]'
                b_data = f'{i}|webm'
                self.formats[b_data] = video_format
                buttons.ibutton(f'{i}-webm', f'ytq {b_data}')
            buttons.ibutton('MP3', 'ytq mp3')
            buttons.ibutton('Audio Formats', 'ytq audio')
            buttons.ibutton('Best Videos', 'ytq bv*+ba/b')
            buttons.ibutton('Best Audios', 'ytq ba/b')
            buttons.ibutton('Cancel', 'ytq cancel', 'footer')
            self.__main_buttons = buttons.build_menu(3)
            msg = f'Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        else:
            format_dict = result.get('formats')
            if format_dict is not None:
                best_quality = 0
                best_format = None
                for item in format_dict:
                    if item.get('tbr') and item.get('height'):
                        tbr = float(item['tbr'])
                        if tbr > best_quality:
                            best_quality = tbr
                            best_format = item['format_id']
                self.qual = best_format
        self.event.set()

    async def back_to_main(self):
        if self.__is_playlist:
            msg = f'Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        else:
            msg = f'Choose Video Quality:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        await editMessage(self.__reply_to, msg, self.__main_buttons)

    async def qual_subbuttons(self, b_name):
        buttons = ButtonMaker()
        tbr_dict = self.formats[b_name]
        for tbr, d_data in tbr_dict.items():
            button_name = f'{tbr}K ({get_readable_file_size(d_data[0])})'
            buttons.ibutton(button_name, f'ytq sub {b_name} {tbr}')
        buttons.ibutton('Back', 'ytq back', 'footer')
        buttons.ibutton('Cancel', 'ytq cancel', 'footer')
        subbuttons = buttons.build_menu(2)
        msg = f'Choose Bit rate for <b>{b_name}</b>:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        await editMessage(self.__reply_to, msg, subbuttons)

    async def mp3_subbuttons(self):
        i = 's' if self.__is_playlist else ''
        buttons = ButtonMaker()
        buttons.ibutton(f'MP3 128K', f'ytq aq 128')
        buttons.ibutton(f'MP3 256K', f'ytq aq 256')
        buttons.ibutton(f'MP3 320K', f'ytq aq 320')
        buttons.ibutton('Back', 'ytq back', 'footer')
        buttons.ibutton('Cancel', 'ytq cancel', 'footer')
        subbuttons = buttons.build_menu(2)
        msg = f'Choose Bit rate for {i}MP3:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        await editMessage(self.__reply_to, msg, subbuttons)

    async def audio_format(self):
        buttons = ButtonMaker()
        buttons.ibutton('Audio Only', 'ytq aq back')
        buttons.ibutton('MP3', 'ytq mp3')
        buttons.ibutton('Custom Quality', 'ytq aq 256')
        buttons.ibutton('Cancel', 'ytq cancel', 'footer')
        audio_buttons = buttons.build_menu(2)
        msg = f'Choose Audio Format:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
        await editMessage(self.__reply_to, msg, audio_buttons)

    async def audio_quality(self, b_name):
        if b_name == 'back':
            await self.audio_format()
        else:
            buttons = ButtonMaker()
            if b_name == '256':
                buttons.ibutton(f'256K', f'ytq aq 256')
                buttons.ibutton(f'320K', f'ytq aq 320')
            else:
                buttons.ibutton(f'128K', f'ytq aq 128')
                buttons.ibutton(f'256K', f'ytq aq 256')
                buttons.ibutton(f'320K', f'ytq aq 320')
            buttons.ibutton('Back', 'ytq audio', 'footer')
            buttons.ibutton('Cancel', 'ytq cancel', 'footer')
            subbuttons = buttons.build_menu(2)
            msg = f'Choose Bit rate for <b>Audio</b>:\nTimeout: {get_readable_time(self.__timeout-(time()-self.__time))}'
            await editMessage(self.__reply_to, msg, subbuttons)


async def extract_info(url):
    ydl_opts = {
        'quiet': True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        return info_dict


async def _mdisk(query, user_id, event, message_id):
    await bot.delete_message(user_id, message_id)
    buttons = ButtonMaker()
    buttons.ibutton('Upload', 'mdisk upload')
    buttons.ibutton('Cancel', 'mdisk cancel', 'footer')
    msg = 'What you want to do with Magnet:\nYou can download it by upload.'
    await sendMessage(user_id, msg, buttons.build_menu(2))
    event.set()


async def _ytdl(query, user_id, event, message_id):
    await bot.delete_message(user_id, message_id)
    await sendMessage(user_id, 'Give me the link of YouTube video or playlist to download.')
    msg = await bot.listen(user_id, timeout=300)
    msg_text = msg.text
    event.set()
    return msg_text


async def ytdl(client, message):
    reply_to = message.message_id
    user_id = message.from_user.id
    yt = YtSelection(client, message)
    await yt.get_quality({'formats': []})
    qual = yt.qual
    if qual is None:
        return
    if not yt.is_cancelled:
        link = await _ytdl(None, user_id, yt.event, reply_to)
        if yt.is_cancelled:
            return
        if is_url(link):
            await editMessage(reply_to, 'Trying to fetch data from YouTube servers. It may take some time.')
            try:
                result = await extract_info(link)
                if result.get('entries') is not None:
                    result = {'entries': result['entries']}
                else:
                    result = {'formats': result['formats']}
                await yt.get_quality(result)
                qual = yt.qual
                if qual is None:
                    return
            except Exception as e:
                LOGGER.error(e, exc_info=True)
                await editMessage(reply_to, 'Failed to fetch data from YouTube servers. Please try again later.')
                return
        else:
            await editMessage(reply_to, 'Invalid YouTube URL. Please try again with a valid URL.')
            return
    await editMessage(reply_to, 'Trying to download the video. It may take some time.')
    ydl_helper = YoutubeDLHelper()
    await ydl_helper.download_youtube_video(link, user_id, reply_to, qual, yt.__is_playlist)


async def ytdlleech(client, message):
    user_id = message.from_user.id
    reply_to = message.message_id
    await bot.delete_message(user_id, reply_to)
    yt = YtSelection(client, message)
    await yt.get_quality({'entries': []})
    qual = yt.qual
    if qual is None:
        return
    if not yt.is_cancelled:
        link = await _ytdl(None, user_id, yt.event, reply_to)
        if yt.is_cancelled:
            return
        if is_url(link):
            try:
                result = await extract_info(link)
                if result.get('entries') is not None:
                    result = {'entries': result['entries']}
                else:
                    result = {'formats': result['formats']}
                await yt.get_quality(result)
                qual = yt.qual
                if qual is None:
                    return
            except Exception as e:
                LOGGER.error(e, exc_info=True)
                await sendMessage(user_id, 'Failed to fetch data from YouTube servers. Please try again later.')
                return
        else:
            await sendMessage(user_id, 'Invalid YouTube URL. Please try again with a valid URL.')
            return
    await sendMessage(user_id, 'Trying to leech the video. It may take some time.')
    ydl_helper = YoutubeDLHelper()
    await ydl_helper.leech_youtube_video(link, user_id, reply_to, qual, yt.__is_playlist)


yt_handlers = [
    MessageHandler(ytdl, filters=command("ytdl") & CustomFilters.auth_users),
    MessageHandler(ytdlleech, filters=command("ytdlleech") & CustomFilters.auth_users),
        ]
    

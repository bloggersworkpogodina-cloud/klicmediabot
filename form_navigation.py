"""Remember actual form branches and prompts, without changing questionnaire content."""
from contextvars import ContextVar
from copy import deepcopy
from html import unescape
import re

from aiogram import BaseMiddleware
from aiogram.methods import SendMessage, SendPhoto, EditMessageText, EditMessageReplyMarkup
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

prompt_transform = None

active_form = ContextVar('active_form', default=None)
FORM_GROUPS = {'BusinessForm', 'CreatorForm', 'ContentCreatorForm', 'RequestForm',
               'AdminBusinessForm', 'AdminRequestForm'}


def is_form(value):
    return bool(value and value.split(':', 1)[0] in FORM_GROUPS)


class FormNavigation(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get('state')
        if state is None:
            return await handler(event, data)
        before = await state.get_state()
        # Command handlers must remain reachable while a text/photo form is active.
        if isinstance(event, Message) and before:
            if event.text and (event.text.startswith('/') or event.text in {'↩️ Вернуться в панель владельца', '❌ Отмена'}):
                await state.clear()
                data['raw_state'] = None
            elif is_form(before) and not event.text and not before.endswith(':photo'):
                await event.answer('На этом шаге нужен текстовый ответ.')
                return
        context = {'state': state, 'chat_id': event.chat.id if isinstance(event, Message) else event.message.chat.id,
                   'prompt': None, 'source': None if isinstance(event, Message) else event.message}
        token = active_form.set(context)
        try:
            result = await handler(event, data)
            current = await state.get_state()
            prompt = context['prompt']
            if is_form(current) and prompt and getattr(event, 'data', None) != 'fix_previous_answer' and not (current == before and isinstance(event, Message)):
                current_data = await state.get_data()
                history = current_data.pop('_form_history', [])
                # Clearing a form starts a new history, even if its first state is unchanged.
                if not is_form(before) or not history:
                    history = []
                if current == before and history:
                    history.pop()
                history.append({'state': current, 'data': deepcopy(current_data), **prompt})
                await state.update_data(_form_history=history)
            return result
        finally:
            active_form.reset(token)


class CaptureFormPrompt:
    async def __call__(self, make_request, bot, method):
        context = active_form.get()
        if context and getattr(method, 'chat_id', None) == context['chat_id']:
            source = context['source']
            if isinstance(method, EditMessageText) and source and source.photo and method.message_id == source.message_id:
                method = SendMessage(chat_id=method.chat_id, text=method.text,
                                     parse_mode=method.parse_mode, reply_markup=method.reply_markup)
            current = await context['state'].get_state()
            if is_form(current) and isinstance(method, (SendMessage, SendPhoto, EditMessageText, EditMessageReplyMarkup)):
                markup = method.reply_markup
                if markup is None or isinstance(markup, InlineKeyboardMarkup):
                    rows = deepcopy(markup.inline_keyboard) if markup else []
                    if not any(b.callback_data == 'fix_previous_answer' for row in rows for b in row):
                        rows.append([InlineKeyboardButton(text='← Исправить ответ', callback_data='fix_previous_answer')])
                    method.reply_markup = InlineKeyboardMarkup(inline_keyboard=rows)
                    text = getattr(method, 'text', None)
                    if text is None:
                        history = (await context['state'].get_data()).get('_form_history', [])
                        text = history[-1]['text'] if history else None
                    if text:
                        if prompt_transform:
                            text, markup = await prompt_transform(current, await context['state'].get_data(), text, method.reply_markup)
                            method.reply_markup = markup
                            if hasattr(method, 'text'):
                                method.text = text
                        context['prompt'] = {'text': text, 'markup': method.reply_markup.model_dump(mode='json')}
        if isinstance(method, SendPhoto) and len(method.caption or '') > 1024:
            text, markup = method.caption, method.reply_markup
            await make_request(bot, method.model_copy(update={'caption': None, 'reply_markup': None}))
            return await self(make_request, bot, SendMessage(chat_id=method.chat_id, text=text,
                                                           parse_mode=method.parse_mode, reply_markup=markup))
        # Long cards keep all content and put actions on the final message.
        if isinstance(method, (SendMessage, EditMessageText)) and len(method.text) > 4000:
            plain = unescape(re.sub(r'<[^>]*>', '', method.text))
            chunks = [plain[i:i + 3900] for i in range(0, len(plain), 3900)]
            markup = method.reply_markup
            result = None
            for i, chunk in enumerate(chunks):
                part = method.model_copy(update={'text': chunk, 'parse_mode': None, 'entities': None,
                                                'reply_markup': markup if i == len(chunks)-1 else None})
                if i and isinstance(part, EditMessageText):
                    part = SendMessage(chat_id=method.chat_id, text=chunk, parse_mode=None,
                                       reply_markup=markup if i == len(chunks)-1 else None)
                result = await make_request(bot, part)
            return result
        return await make_request(bot, method)

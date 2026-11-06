"""Owner dashboard, search, role switching and reviewable administrative actions."""
import asyncio
import json
import re
import secrets
from html import escape
from aiogram import F
from aiogram.filters import StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.methods import SendMessage, SendPhoto, EditMessageText, EditMessageReplyMarkup
from interface_v39 import keyboard, button, clean, form_state


class StyleMessages:
    def __init__(self, owner):
        self.owner=owner

    async def __call__(self, make_request, bot, method):
        markup=getattr(method,'reply_markup',None)
        if isinstance(markup,InlineKeyboardMarkup):
            rows=[]
            for row in markup.inline_keyboard:
                updated=[]
                for original in row:
                    data=original.callback_data or ''
                    label=original.text.replace('заявки','проекты').replace('Заявки','Проекты').replace('заявку','проект').replace('Заявку','Проект')
                    danger=data.startswith(('decline:','moderate_reject:','profile_reject:','owner_delete_project:','owner_delete_confirm:'))
                    positive=data.startswith(('accept:','moderate_approve:','profile_accept:','respond:','owner_filter_send_confirm:','own:broadcastsend:','own:complete:')) or data in {'ui:submit','ui:optiondone','ui:resume'} or data.endswith(':done')
                    style='danger' if danger else 'success' if positive else None
                    updated.append(original.model_copy(update={'text':label,'style':style}))
                rows.append(updated)
            chat=getattr(method,'chat_id',None)
            if isinstance(chat,int) and self.owner.n['is_admin'](chat) and self.owner.mode(chat):
                if not any(b.callback_data=='owner_return' for row in rows for b in row):
                    rows.append([button('↩️ В панель владельца','owner_return')])
            method.reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        return await make_request(bot,method)


class OwnerInterface:
    AUDIENCES={'all':'Все пользователи','business':'Бизнесы','blogger':'Блогеры','content':'Контент-креаторы'}
    MODES={'business':'бизнеса','blogger':'блогера','content':'контент-креатора'}

    def __init__(self,n):
        self.n=n;self.ui=n['ui'];self.lock=asyncio.Lock()

    def install(self):
        self.old_init=self.n['init_db'];self.n['init_db']=self.init
        r=self.n['router'];start=len(r.callback_query.handlers)
        r.callback_query.register(self.callback,F.data.startswith('own:'))
        r.callback_query.register(self.callback,F.data.in_({'owner_mode_business','owner_mode_blogger','owner_mode_content','owner_return'}))
        r.callback_query.register(self.callback,F.data.startswith('complete_project:'))
        r.callback_query.register(self.callback,F.data.startswith('owner_complete_project:'))
        r.callback_query.handlers[:]=r.callback_query.handlers[start:]+r.callback_query.handlers[:start]
        start=len(r.message.handlers)
        r.message.register(self.command,F.text.in_({'📣 Рассылка','🔎 Поиск','↩️ В панель владельца'}))
        r.message.register(self.message,StateFilter('OwnerFlow:search','OwnerFlow:broadcast_text','OwnerFlow:broadcast_preview'))
        r.message.handlers[:]=r.message.handlers[start:]+r.message.handlers[:start]

    def init(self):
        self.old_init()
        for sql in [
            'CREATE TABLE IF NOT EXISTS owner_modes(user_id BIGINT PRIMARY KEY,mode TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS owner_profiles(user_id BIGINT NOT NULL,creator_type TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(user_id,creator_type))',
            'CREATE TABLE IF NOT EXISTS owner_broadcasts(token TEXT PRIMARY KEY,owner_id BIGINT NOT NULL,body TEXT NOT NULL,audience TEXT NOT NULL,status TEXT NOT NULL,recipients TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0)',
        ]:self.ui.execute(sql)

    def mode(self,uid):
        row=self.ui.one('SELECT mode FROM owner_modes WHERE user_id=?',(uid,))
        return row['mode'] if row else None

    async def dashboard(self,message,uid,state=None):
        if not self.n['is_admin'](uid):return
        if state:
            if form_state(await state.get_state()):await self.ui.save_draft(uid,state)
            await state.clear()
        self.ui.execute('DELETE FROM owner_modes WHERE user_id=?',(uid,))
        counts={}
        for kind,table,extra,params in [
            ('blogger','creators'," AND creator_type=?",('Блогер',)),
            ('content','creators'," AND creator_type=?",('Контент-креатор',)),
            ('business','business_profiles','',()),('projects','requests','',())]:
            counts[kind]=self.ui.one(f"SELECT COUNT(*) AS n FROM {table} WHERE status='pending'"+extra,params)['n']
        questions=self.ui.one("SELECT COUNT(*) AS n FROM support_tickets WHERE status='new'")['n']
        text=f"<b>КЛИК · Панель владельца</b>\n\nНовые анкеты: <b>{counts['blogger']+counts['content']+counts['business']}</b>\nПроекты на проверке: <b>{counts['projects']}</b>\nНовые вопросы: <b>{questions}</b>"
        await message.answer(text,reply_markup=self.n['owner_reply_kb']())
        await message.answer('Что проверить?',reply_markup=keyboard(
            (f"Блогеры · {counts['blogger']}",'profile_mod:blogger'),
            (f"Контент-креаторы · {counts['content']}",'profile_mod:content'),
            (f"Бизнесы · {counts['business']}",'profile_mod:business'),
            (f"Проекты · {counts['projects']}",'profile_mod:projects'),
            (f'Вопросы · {questions}','support_list:new'),('🔎 Поиск','own:search'),('Обновить счётчики','own:dashboard')))

    async def mode_home(self,message,uid):
        mode=self.mode(uid)
        if not mode:return await self.dashboard(message,uid)
        markup=self.n['business_menu_kb'](show_owner_return=True) if mode=='business' else self.n['creator_menu_kb']()
        await message.answer(f'<b>Вы в режиме {self.MODES[mode]}</b>',reply_markup=markup)

    async def switch_mode(self,c,state,mode):
        uid=c.from_user.id
        if not self.n['is_admin'](uid):return
        if form_state(await state.get_state()):await self.ui.save_draft(uid,state)
        # Save each creator role before switching; registrations cannot overwrite its other role.
        conn=self.n['db']()
        try:
            current=conn.execute('SELECT * FROM creators WHERE user_id=?',(uid,)).fetchone()
            if current:
                row=dict(current)
                conn.execute('INSERT INTO owner_profiles(user_id,creator_type,payload) VALUES (?,?,?) ON CONFLICT(user_id,creator_type) DO UPDATE SET payload=excluded.payload',(uid,row['creator_type'],json.dumps(row,ensure_ascii=False)))
            target='Блогер' if mode=='blogger' else 'Контент-креатор'
            if mode!='business':
                saved=conn.execute('SELECT payload FROM owner_profiles WHERE user_id=? AND creator_type=?',(uid,target)).fetchone()
                if saved and current and current['creator_type']!=target:
                    row=json.loads(saved['payload']);columns=[k for k in dict(current) if k!='user_id' and k in row]
                    conn.execute('UPDATE creators SET '+','.join(k+'=?' for k in columns)+' WHERE user_id=?',tuple(row[k] for k in columns)+(uid,))
                    current=row
            conn.execute('INSERT INTO owner_modes(user_id,mode) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode',(uid,mode))
            conn.commit()
        finally:conn.close()
        await state.clear()
        await c.message.answer(f'<b>Вы в режиме {self.MODES[mode]}</b>\nВернуться в панель можно кнопкой под сообщением или внизу экрана.',reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text='↩️ В панель владельца')]],resize_keyboard=True,is_persistent=True,input_field_placeholder=f'Режим {self.MODES[mode]}'))
        exists=self.ui.one('SELECT user_id FROM business_profiles WHERE user_id=?',(uid,)) if mode=='business' else current and current['creator_type']==target
        if exists:
            await self.mode_home(c.message,uid)
        else:
            entry={'business':'role_business','blogger':'role_creator','content':'content_creator_start'}[mode]
            await self.n[entry](c,state)

    def audience(self,kind):
        if kind=='all':return [r['user_id'] for r in self.ui.rows('SELECT user_id FROM users ORDER BY user_id')]
        if kind=='business':return [r['user_id'] for r in self.ui.rows('SELECT user_id FROM business_profiles ORDER BY user_id')]
        if kind in {'blogger','content'}:
            return [r['user_id'] for r in self.ui.rows('SELECT user_id FROM creators WHERE creator_type=? ORDER BY user_id',('Блогер' if kind=='blogger' else 'Контент-креатор',))]
        return []

    async def broadcast_start(self,message,uid,state):
        if not self.n['is_admin'](uid):return
        await state.clear()
        await message.answer('<b>Рассылка</b>\nКому отправить сообщение?',reply_markup=keyboard(*[(label,f'own:audience:{kind}') for kind,label in self.AUDIENCES.items()],('Отмена','own:cancel')))

    async def broadcast_preview(self,message,uid,state,body):
        data=await state.get_data();kind=data.get('_audience')
        if kind not in self.AUDIENCES:return
        if len(message.text or '')>3900:
            await message.answer('Сократите текст до 3900 символов, чтобы рассылка пришла одним сообщением.');return
        recipients=self.audience(kind);token=secrets.token_hex(8)
        # Invalidate the previous preview when editing so its old send button cannot send stale text.
        old=data.get('_broadcast_token')
        if old:self.ui.execute("UPDATE owner_broadcasts SET status='cancelled' WHERE token=? AND owner_id=? AND status='draft'",(old,uid))
        self.ui.execute('INSERT INTO owner_broadcasts(token,owner_id,body,audience,status,recipients) VALUES (?,?,?,?,?,?)',(token,uid,body,kind,'draft',json.dumps(recipients)))
        await state.set_state('OwnerFlow:broadcast_preview');await state.update_data(_broadcast_token=token,_broadcast_body=body)
        await message.answer('<b>Предпросмотр сообщения</b>')
        await message.answer(body)
        await message.answer(f'Аудитория: {self.AUDIENCES[kind]}\nПолучателей: {len(recipients)}\nСообщение ещё не отправлено.',reply_markup=keyboard(
            ('Отправить',f'own:broadcastsend:{token}','success'),('Изменить текст','own:broadcastedit'),('Изменить аудиторию','own:broadcastaudience'),('Отмена','own:cancel')))

    async def send_broadcast(self,c,state,bot,token):
        uid=c.from_user.id
        if not self.n['is_admin'](uid):return
        async with self.lock:
            data=await state.get_data()
            if await state.get_state()!='OwnerFlow:broadcast_preview' or data.get('_broadcast_token')!=token:
                await c.message.answer('Этот предпросмотр уже недействителен. Откройте новую рассылку.');return
            changed=self.ui.execute("UPDATE owner_broadcasts SET status='sending' WHERE token=? AND owner_id=? AND status='draft'",(token,uid))
            if not changed:
                await c.message.answer('Эта рассылка уже отправляется, отправлена или отменена.');return
            row=self.ui.one('SELECT * FROM owner_broadcasts WHERE token=?',(token,));sent=failed=0
            await state.clear()
            await c.message.edit_reply_markup(reply_markup=None)
            await c.message.answer('Рассылка началась.')
            try:
                for recipient in json.loads(row['recipients']):
                    try:
                        await bot.send_message(recipient,row['body']);sent+=1
                    except Exception:
                        failed+=1;self.n['logger'].exception('Broadcast failed: recipient=%s',recipient)
                    self.ui.execute('UPDATE owner_broadcasts SET sent=?,failed=? WHERE token=?',(sent,failed,token))
                    await asyncio.sleep(0.05)
                self.ui.execute("UPDATE owner_broadcasts SET status='sent' WHERE token=?",(token,))
            except Exception:
                self.n['logger'].exception('Broadcast interrupted: %s',token)
                self.ui.execute("UPDATE owner_broadcasts SET status='interrupted' WHERE token=?",(token,))
                await c.message.answer(f'Рассылка прервана.\nОтправлено: {sent}\nНе доставлено: {failed}. Повторная отправка автоматически не запускается.');return
            await c.message.answer(f'Рассылка завершена.\nОтправлено: {sent}\nНе доставлено: {failed}',reply_markup=keyboard(('В панель владельца','own:dashboard')))

    async def complete_prompt(self,c,state,rid,owner=False):
        uid=c.from_user.id;r=self.ui.one('SELECT * FROM requests WHERE request_id=?',(rid,))
        if not r or (not self.n['is_admin'](uid) if owner else r['business_user_id']!=uid):
            await c.message.answer('Нет доступа к проекту.');return
        if r['status']!='active':
            await c.message.answer('Проект уже завершён или не активен.');return
        token=secrets.token_hex(8)
        await state.update_data(_complete={'id':rid,'owner':owner,'token':token})
        await c.message.answer(f"<b>Завершить проект #{rid}?</b>\n{clean(r['business_name'])}\n\nПриём откликов и рассылка остановятся. Итоги и отклики сохранятся в архиве.",reply_markup=keyboard(
            ('Да, завершить',f'own:complete:{token}','success'),('Не завершать','own:completecancel')))

    async def search(self,message,uid,state,query,page=0):
        if not self.n['is_admin'](uid):return
        query=query.strip()
        if not query:await message.answer('Введите имя, название бизнеса или номер проекта.');return
        await state.update_data(_search=query)
        rows=[];needle=query.casefold();number=query.lstrip('#').strip()
        for kind,table,fields in [('creator','creators',['name','social_link','contact']),('business','business_profiles',['business_name','social_link','contact']),('manual','admin_businesses',['business_name','social_link','contact']),('project','requests',['business_name','task'])]:
            for row in self.ui.rows('SELECT * FROM '+table):
                exact=kind=='project' and number.isdigit() and row['request_id']==int(number)
                if exact or any(needle in str(row.get(k) or '').casefold() for k in fields):rows.append((kind,row,exact))
        rows.sort(key=lambda item:not item[2]);subset,page=self.ui.page(rows,page)
        await message.answer(f'<b>Поиск: {clean(query)}</b>\nНайдено: {len(rows)} · Страница {page+1}',reply_markup=self.ui.pagination('own:searchpage',page,len(rows)))
        for kind,row,_ in subset:
            if kind=='creator':
                await self.n['send_creator_card'](message,row,include_contact=True)
            elif kind in {'business','manual'}:
                actions=[('Новый поиск','own:search')]
                if kind=='manual':actions.insert(0,('Создать проект',f"admin_request_for:{row['business_id']}"))
                await message.answer(f"<b>{clean(row['business_name'])}</b>\n{clean(row.get('city'))}\n{clean(row.get('contact'))}",reply_markup=keyboard(*actions))
            else:
                markup=self.n['owner_project_kb'](row['request_id'],'completed' if row['status']=='active' and not self.n['project_distribution_open'](row) else row['status'])
                await message.answer(self.ui.short_card(row),reply_markup=markup)

    async def command(self,message,state):
        uid=message.from_user.id
        if not self.n['is_admin'](uid):return
        if message.text=='📣 Рассылка':await self.broadcast_start(message,uid,state)
        elif message.text=='🔎 Поиск':
            await state.set_state('OwnerFlow:search');await message.answer('Введите имя креатора, название бизнеса или номер проекта, например #12.',reply_markup=keyboard(('В панель владельца','own:dashboard')))
        else:await self.dashboard(message,uid,state)

    async def message(self,message,state):
        uid=message.from_user.id
        if not self.n['is_admin'](uid):return
        current=await state.get_state()
        if current=='OwnerFlow:search':await self.search(message,uid,state,message.text or '')
        elif current=='OwnerFlow:broadcast_text':
            if not message.text or not message.text.strip():await message.answer('Пришлите текст одним сообщением.');return
            await self.broadcast_preview(message,uid,state,message.html_text or message.text)
        else:await message.answer('Проверьте предпросмотр и выберите действие кнопкой.')

    async def callback(self,callback,state,bot):
        c=callback;uid=c.from_user.id;code=c.data;p=code.split(':')
        # Business owners may confirm only their own project; every other action is admin-only.
        if not code.startswith(('complete_project:','own:complete:','own:completecancel')) and not self.n['is_admin'](uid):
            await c.answer('Нет доступа',show_alert=True);return
        await c.answer()
        if code.startswith('complete_project:'):await self.complete_prompt(c,state,int(p[1]));return
        if code.startswith('owner_complete_project:'):await self.complete_prompt(c,state,int(p[1]),owner=True);return
        if code=='own:completecancel':
            await state.update_data(_complete=None);await c.message.edit_text('Завершение отменено.',reply_markup=keyboard(('К проектам','admin_requests' if self.n['is_admin'](uid) else 'business_my_requests')));return
        if code.startswith('own:complete:'):
            pending=(await state.get_data()).get('_complete')
            if not pending or pending['token']!=p[2]:await c.message.answer('Это подтверждение уже недействительно.');return
            await state.update_data(_complete=None)
            fn='owner_complete_project' if pending['owner'] else 'complete_project'
            await self.n[fn](c.model_copy(update={'data':f"{'owner_complete_project' if pending['owner'] else 'complete_project'}:{pending['id']}"}));return
        if code in {'own:dashboard','owner_return'}:await self.dashboard(c.message,uid,state);return
        if code.startswith('owner_mode_'):await self.switch_mode(c,state,code.removeprefix('owner_mode_'));return
        if code=='own:search':
            await state.set_state('OwnerFlow:search');await c.message.answer('Введите имя, бизнес или номер проекта.',reply_markup=keyboard(('В панель владельца','own:dashboard')));return
        if code.startswith('own:searchpage:'):
            await self.search(c.message,uid,state,(await state.get_data()).get('_search',''),int(p[2]));return
        if code=='own:cancel':
            data=await state.get_data()
            if data.get('_broadcast_token'):self.ui.execute("UPDATE owner_broadcasts SET status='cancelled' WHERE token=? AND owner_id=? AND status='draft'",(data['_broadcast_token'],uid))
            await state.clear();await c.message.edit_text('Рассылка отменена.',reply_markup=keyboard(('В панель владельца','own:dashboard')));return
        if code=='own:broadcastaudience':
            data=await state.get_data()
            if data.get('_broadcast_token'):self.ui.execute("UPDATE owner_broadcasts SET status='cancelled' WHERE token=? AND owner_id=? AND status='draft'",(data['_broadcast_token'],uid))
            await c.message.answer('Выберите новую аудиторию. Текст сохранён.',reply_markup=keyboard(*[(label,f'own:audience:{kind}') for kind,label in self.AUDIENCES.items()],('Отмена','own:cancel')));return
        if code.startswith('own:audience:'):
            kind=p[2]
            if kind not in self.AUDIENCES:return
            await state.set_state('OwnerFlow:broadcast_text');await state.update_data(_audience=kind)
            saved=(await state.get_data()).get('_broadcast_body')
            if saved:
                await self.broadcast_preview(c.message.model_copy(update={'text':saved}),uid,state,saved);return
            await c.message.answer(f'<b>{self.AUDIENCES[kind]}</b>\nПришлите текст рассылки. Перед отправкой будет предпросмотр.',reply_markup=keyboard(('Отмена','own:cancel')));return
        if code=='own:broadcastedit':
            data=await state.get_data()
            if not data.get('_broadcast_token'):return
            self.ui.execute("UPDATE owner_broadcasts SET status='cancelled' WHERE token=? AND owner_id=? AND status='draft'",(data['_broadcast_token'],uid))
            await state.set_state('OwnerFlow:broadcast_text');await c.message.answer('Пришлите исправленный текст.',reply_markup=keyboard(('Отмена','own:cancel')));return
        if code.startswith('own:broadcastsend:'):await self.send_broadcast(c,state,bot,p[2]);return

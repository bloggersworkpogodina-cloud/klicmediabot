"""User interface workflows: review, field editing, drafts, browsing and moderation."""
import calendar
import inspect
import json
import re
from datetime import date, timedelta
from html import escape

from aiogram import BaseMiddleware, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from persistent_state import PersistentStorage

LABELS = {
 'business_name':'Название бизнеса', 'name':'Имя', 'photo_file_id':'Фото', 'country':'Страна',
 'region':'Регион', 'city':'Город', 'project_geography':'География проекта', 'niche':'Тематика',
 'creator_type':'Роль', 'creator_needed':'Кого ищем', 'work_format':'Формат работы', 'travel_scope':'Выезды',
 'social_link':'Соцсети / блог №1', 'followers':'Подписчики', 'reach':'Средний охват',
 'blog2_platform':'Площадка второго блога', 'blog2_link':'Второй блог', 'blog2_followers':'Подписчики второго блога',
 'blog2_reach':'Охват второго блога', 'ad_formats':'Рекламные форматы', 'content_types':'Форматы контента',
 'industries':'Ниши', 'on_camera':'В кадре', 'creator_skills':'Навыки / работы', 'portfolio_link':'Портфолио',
 'delivery_available':'Получение товаров', 'cooperation_formats':'Условия сотрудничества',
 'cooperation_format':'Условия сотрудничества', 'price':'Стоимость', 'excluded_topics':'Исключённые темы',
 'brief_ready':'Работа по ТЗ', 'contact':'Контакт', 'task':'Задача', 'title':'Название проекта',
 'budget':'Бюджет', 'content_quantity':'Количество контента', 'requirements':'Требования',
 'product_provided':'Товар / услуга', 'response_deadline':'Отклики до', 'delivery_deadline':'Срок выполнения',
 'extra_info':'Дополнительная информация', 'blogger_niches':'Тематика блогов', 'blogger_platforms':'Площадки',
 'blogger_followers':'Подписчики от', 'blogger_reach':'Охват от', 'blogger_ad_formats':'Рекламные форматы',
}
STATUS = {'new':'Ожидает решения', 'accepted':'Принят', 'declined':'Отклонён', 'pending':'На проверке',
          'active':'Одобрен', 'rejected':'Нужны исправления', 'completed':'Проект завершён', 'closed':'Закрыт'}
FINAL = {'BusinessForm:contact':('business_contact','contact'), 'CreatorForm:contact':('creator_contact','contact'),
 'ContentCreatorForm:contact':('cc_contact','contact'), 'RequestForm:blogger_extra':('blogger_req_extra','extra_info'),
 'RequestForm:extra_info':('req_extra_info','extra_info'), 'AdminRequestForm:budget':('admin_request_budget','budget'),
 'AdminBusinessForm:contact':('admin_business_contact','contact')}
DATE_HANDLERS = {'RequestForm:blogger_response_deadline':'blogger_req_response_deadline',
 'RequestForm:response_deadline':'req_response_deadline', 'RequestForm:blogger_publication_deadline':'blogger_req_publication_deadline',
 'RequestForm:delivery_deadline':'req_delivery_deadline'}


def button(text, data, style=None):
    return InlineKeyboardButton(text=text, callback_data=data, **({'style':style} if style else {}))


def keyboard(*items):
    return InlineKeyboardMarkup(inline_keyboard=[[button(*item)] for item in items])


def clean(value):
    return escape(str(value if value not in (None, '') else '—'))


def form_state(value):
    return bool(value and (value.split(':')[0] in {'BusinessForm','CreatorForm','ContentCreatorForm','RequestForm','AdminBusinessForm','AdminRequestForm','UIFlow'}))


class InterfaceMiddleware(BaseMiddleware):
    def __init__(self, ui):
        self.ui = ui

    async def __call__(self, handler, event, data):
        state = data.get('state')
        if not state:
            return await handler(event, data)
        uid = event.from_user.id
        before = await state.get_state()
        if isinstance(event, Message) and event.text and event.text.split() and event.text.split()[0].split('@')[0] in {'/start','/menu'}:
            if form_state(before):
                await self.ui.save_draft(uid, state)
            draft = self.ui.one('SELECT payload FROM ui_drafts WHERE user_id=?', (uid,))
            if draft:
                await state.clear()
                await event.answer('У вас есть незавершённая анкета или проект. Продолжить заполнение?',
                    reply_markup=keyboard(('Продолжить','ui:resume','success'),('Начать заново','ui:restart'),('В меню','ui:home')))
                return
        result = await handler(event, data)
        after = await state.get_state()
        if form_state(after):
            await self.ui.save_draft(uid, state)
        elif form_state(before) and not (getattr(event, 'data', '') in {'ui:save','ui:home'}):
            self.ui.execute('DELETE FROM ui_drafts WHERE user_id=?', (uid,))
        return result


class Interface:
    def __init__(self, ns):
        self.n = ns

    def rows(self, sql, params=()):
        conn = self.n['db']()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        conn = self.n['db']()
        try:
            result = conn.execute(sql, params)
            conn.commit()
            return result.rowcount
        finally:
            conn.close()

    def init(self):
        self.original_init()
        for sql in [
            'CREATE TABLE IF NOT EXISTS dialog_state(storage_key TEXT PRIMARY KEY,state TEXT,data TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS ui_drafts(user_id BIGINT PRIMARY KEY,payload TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS ui_notes(kind TEXT NOT NULL,item_id BIGINT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(kind,item_id))',
            'CREATE TABLE IF NOT EXISTS ui_preferences(user_id BIGINT PRIMARY KEY,projects INTEGER NOT NULL DEFAULT 1)',
        ]:
            self.execute(sql)

    def install(self):
        self.original_init = self.n['init_db']
        self.n['init_db'] = self.init
        router = self.n['router']
        # Specific UI handlers precede legacy callbacks, preserving their public callback ids.
        start = len(router.callback_query.handlers)
        router.callback_query.register(self.callback, F.data.startswith('ui:'))
        router.callback_query.register(self.callback, F.data.in_({'business_my_requests','business_responses','creator_my_responses',
            'creator_view_requests','business_profile','creator_profile','business_edit_profile','creator_edit_profile','creator_settings',
            'registered_businesses','admin_business_list','admin_request_choose_business'}))
        router.callback_query.register(self.callback, F.data.startswith('media_base:'))
        router.callback_query.register(self.callback, F.data.startswith('support_list:'))
        router.callback_query.register(self.callback, F.data.startswith('owner_project_responses:'))
        router.callback_query.register(self.callback, F.data.startswith('profile_reject:'))
        router.callback_query.register(self.callback, F.data.startswith('moderate_reject:'))
        router.callback_query.handlers[:] = router.callback_query.handlers[start:] + router.callback_query.handlers[:start]
        start = len(router.message.handlers)
        from aiogram.filters import StateFilter
        router.message.register(self.message, StateFilter('UIFlow:field','UIFlow:reason','UIFlow:review','UIFlow:fields','UIFlow:date'))
        router.message.register(self.admin_message, F.text.in_({'💬 Все отклики','🔗 Рефералы'}))
        router.message.handlers[:] = router.message.handlers[start:] + router.message.handlers[:start]
        router.message.outer_middleware._middlewares.insert(0, InterfaceMiddleware(self))
        router.callback_query.outer_middleware._middlewares.insert(0, InterfaceMiddleware(self))
        import form_navigation
        form_navigation.prompt_transform = self.transform

    async def save_draft(self, uid, state):
        payload = json.dumps({'state':await state.get_state(),'data':await state.get_data()}, ensure_ascii=False)
        self.execute('INSERT INTO ui_drafts(user_id,payload) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload', (uid,payload))

    async def home(self, message, uid, state):
        if form_state(await state.get_state()):
            await self.save_draft(uid, state)
        await state.clear()
        creator = self.one('SELECT * FROM creators WHERE user_id=?',(uid,))
        business = self.one('SELECT * FROM business_profiles WHERE user_id=?',(uid,))
        if self.n['is_admin'](uid):
            await self.n['owner_ui'].mode_home(message,uid)
        elif creator:
            await message.answer('<b>КЛИК · Личный кабинет</b>\nПрофиль: '+STATUS.get(creator['status'],'На проверке'), reply_markup=self.n['creator_menu_kb']())
        elif business:
            await message.answer('<b>КЛИК · Кабинет бизнеса</b>\nПрофиль: '+STATUS.get(business['status'],'На проверке'), reply_markup=self.n['business_menu_kb']())
        else:
            await message.answer('<b>КЛИК</b>\nВыберите вашу роль:',reply_markup=self.n['main_menu_kb']())
        if self.one('SELECT user_id FROM ui_drafts WHERE user_id=?',(uid,)):
            await message.answer('Черновик сохранён.',reply_markup=keyboard(('Продолжить заполнение','ui:resume')))

    async def transform(self, current, data, text, markup):
        if not current or current.startswith('UIFlow:'):
            return text, markup
        text = re.sub(r'^\d+\.\s*', '', text or '')
        if current.startswith('RequestForm:'):
            field = current.split(':')[1]
            stage = 'Кого ищем' if field in {'creator_needed','photo','blogger_geography','blogger_niches','blogger_platforms','blogger_followers','blogger_reach','blogger_ad_formats'} else 'Условия' if any(x in field for x in ('budget','deadline','cooperation','extra')) else 'Задача'
            if not text.startswith('<i>Проект ·'):
                text = f'<i>Проект · {stage}</i>\n\n'+text
        rows = [list(row) for row in markup.inline_keyboard] if markup else []
        history = data.get('_form_history', [])
        rows = [[button('← Назад',b.callback_data) if b.callback_data == 'fix_previous_answer' else b for b in row] for row in rows]
        if not history:
            rows = [[b for b in row if b.callback_data != 'fix_previous_answer'] for row in rows]
        rows = [row for row in rows if row]
        if current in DATE_HANDLERS:
            text = re.sub(r'Напишите дату[^\n]*', 'Выберите срок кнопкой или напишите дату с годом.',text)
            rows.insert(0,[button('Через 3 дня','ui:date:3'),button('Через неделю','ui:date:7')])
            rows.insert(1,[button('📅 Выбрать дату','ui:calendar')])
        if not any(b.callback_data=='ui:save' for row in rows for b in row):
            rows.append([button('Сохранить и выйти','ui:save')])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def preview(self, message, state, handler, final_field):
        data = await state.get_data()
        if data.get('_confirmed'):
            return False
        final = (message.text or '').strip()
        if final_field == 'extra_info' and final.lower() in {'пропустить','нет','-'}:
            final = None
        data[final_field] = final
        data['_submit'] = handler
        data['_answer'] = message.text or ''
        data['_final_state'] = await state.get_state()
        await state.set_data(data)
        await state.set_state('UIFlow:review')
        await self.review(message, state)
        return True

    async def review(self, message, state):
        data=await state.get_data()
        values=data.get('_edit_values',data)
        lines=['<b>Проверьте данные перед отправкой</b>']
        for key,value in values.items():
            if key in LABELS and key != 'photo_file_id' and value not in (None,''):
                lines.append(f'<b>{LABELS[key]}:</b> {clean(value)}')
        photo=values.get('photo_file_id')
        if photo:
            await message.answer_photo(photo)
        await state.set_state('UIFlow:review')
        await message.answer('\n'.join(lines),reply_markup=keyboard(
            ('Отправить на модерацию','ui:submit','success'),('Изменить','ui:editreview'),('Сохранить и выйти','ui:save')))

    def allowed_fields(self, table, row):
        if table=='business_profiles':
            return ['business_name','city','niche','social_link','contact']
        if table=='requests':
            return [k for k in ['photo_file_id','city','niche','task','work_format','content_types','content_quantity','on_camera',
                'requirements','creator_skills','product_provided','cooperation_format','budget','response_deadline','delivery_deadline','extra_info'] if k in row]
        common=['name','photo_file_id','country','region','city','work_format','travel_scope','cooperation_formats','price','excluded_topics','contact']
        if row.get('creator_type')=='Контент-креатор':
            return common+['content_types','industries','on_camera','creator_skills','portfolio_link','delivery_available']
        return common+['niche','social_link','followers','reach','blog2_platform','blog2_link','blog2_followers','blog2_reach','ad_formats','brief_ready']

    async def start_edit(self, message, uid, state, table, item=None):
        key='request_id' if table=='requests' else 'user_id'
        item=uid if item is None else item
        row=self.one(f'SELECT * FROM {table} WHERE {key}=?',(item,))
        if not row or (row.get('business_user_id') if table=='requests' else row['user_id']) != uid:
            await message.answer('Запись недоступна.'); return
        if table=='requests' and row['status'] not in {'rejected','pending'}:
            await message.answer('Исправления здесь доступны для проектов на проверке или возвращённых на доработку.'); return
        await state.clear()
        await state.update_data(_edit_table=table,_edit_id=item,_edit_values=row,_edit_fields=self.allowed_fields(table,row))
        await self.edit_fields(message,state)

    async def edit_fields(self, message, state):
        data=await state.get_data()
        fields=data.get('_edit_fields') or [k for k in data if k in LABELS and k!='creator_type']
        # Role determines branching. Changing it restarts the form instead of leaving incompatible answers.
        fields=[k for k in fields if k!='creator_needed']
        await state.update_data(_edit_fields=fields)
        await state.set_state('UIFlow:fields')
        rows=[[button(LABELS[k],f'ui:field:{i}')] for i,k in enumerate(fields)]
        rows += [[button('Проверить изменения','ui:review','success')],[button('Сохранить и выйти','ui:save')]]
        await message.answer('Что изменить? Остальные ответы сохранятся.\nПосле отправки изменения пройдут модерацию.',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    def field_options(self, field):
        constants={'niche':'BLOG_NICHES','content_types':'CONTENT_TYPES','industries':'CONTENT_INDUSTRIES',
          'creator_skills':'CONTENT_SKILLS','ad_formats':'AD_FORMATS','cooperation_formats':'COOP_FORMATS',
          'blogger_niches':'BLOGGER_REQUEST_NICHES','blogger_platforms':'BLOGGER_REQUEST_PLATFORMS','blogger_ad_formats':'BLOGGER_REQUEST_AD_FORMATS'}
        if field in constants:
            return self.n[constants[field]],True
        singles={'work_format':['Онлайн','Оффлайн','Онлайн + оффлайн'], 'on_camera':['Да','Нет','Неважно'],
          'delivery_available':['Да','Нет'],'brief_ready':['Да','Нет'], 'cooperation_format':['Оплата','Бартер','Оплата + бартер','% с продаж'],
          'travel_scope':['Только мой город','По региону','По России','По всему миру']}
        return singles.get(field,[]),False

    async def field_prompt(self,message,state,index):
        data=await state.get_data(); fields=data.get('_edit_fields',[])
        if index<0 or index>=len(fields): return
        field=fields[index]
        values=data.get('_edit_values',data)
        options,multi=self.field_options(field)
        selected=[x for x in options if x in (values.get(field) or '').split(', ')]
        await state.update_data(_field=field,_options=options,_multi=multi,_selected=selected)
        await state.set_state('UIFlow:field')
        await self.field_keyboard(message,state)

    async def field_keyboard(self,message,state):
        data=await state.get_data();field=data['_field'];options=data['_options']
        rows=[[button(('✓ ' if x in data['_selected'] else '')+x,f'ui:option:{i}')] for i,x in enumerate(options)]
        if data['_multi']: rows.append([button('✅ ГОТОВО','ui:optiondone','success')])
        if field in {'blogger_followers','blogger_reach','project_geography','requirements','extra_info','excluded_topics','price','budget'}:
            label='Неважно' if field.startswith('blogger_') or field=='project_geography' else 'Обсуждается' if field in {'price','budget'} else 'Пропустить' if field in {'extra_info','excluded_topics'} else 'Нет требований'
            rows.append([button(label,'ui:fieldskip')])
        rows += [[button('← К полям','ui:editreview')],[button('Сохранить и выйти','ui:save')]]
        values=data.get('_edit_values',data)
        await message.answer(f'<b>{LABELS[field]}</b>\nСейчас: {clean("Фото добавлено" if field=="photo_file_id" and values.get(field) else values.get(field))}\n'
            +('Пришлите одно фото.' if field=='photo_file_id' else 'Выберите вариант.' if options else 'Напишите новое значение.'),reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def set_field(self,message,state,value):
        data=await state.get_data(); field=data['_field']
        if field in {'response_deadline','delivery_deadline'}:
            dt=self.n['parse_project_deadline'](value)
            if field=='response_deadline' and (not dt or dt<self.today()):
                await message.answer('Укажите сегодняшнюю или будущую дату с годом.'); return
            if dt: value=dt.strftime('%d.%m.%Y')
        if '_edit_values' in data:
            values=data['_edit_values'];values[field]=value
            await state.update_data(_edit_values=values)
        else:
            await state.update_data(**{field:value})
            if field==FINAL.get(data.get('_final_state'),('',None))[1]:
                await state.update_data(_answer=value or 'Пропустить')
        await self.edit_fields(message,state)

    def today(self):
        return self.n['datetime'].now(self.n['MSK_TZ']).date()

    async def calendar(self,message,state,year=None,month=None):
        today=self.today();year=year or today.year;month=month or today.month
        current=await state.get_state();data=await state.get_data()
        if current in DATE_HANDLERS:
            await state.update_data(_date_state=current)
        elif current!='UIFlow:date' or not data.get('_date_state'):
            await message.answer('Сначала откройте вопрос о сроке.');return
        await state.set_state('UIFlow:date')
        rows=[]
        for week in calendar.monthcalendar(year,month):
            rows.append([button(str(day) if day else '·',f'ui:pick:{year}-{month:02}-{day:02}' if day else 'ui:noop') for day in week])
        prev=date(year,month,1)-timedelta(days=1)
        nxt=(date(year,month,28)+timedelta(days=4)).replace(day=1)
        rows.append([button('←',f'ui:month:{prev.year}:{prev.month}'),button('→',f'ui:month:{nxt.year}:{nxt.month}')])
        rows.append([button('Сохранить и выйти','ui:save')])
        months=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
        await message.answer(f'<b>{months[month-1]} {year}</b>\nПн · Вт · Ср · Чт · Пт · Сб · Вс',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def apply_date(self,message,state,value):
        current=await state.get_state();data=await state.get_data();target=data.get('_date_state') if current=='UIFlow:date' else current
        if target not in DATE_HANDLERS:return
        dt=self.n['parse_project_deadline'](value)
        if not dt or dt<self.today():
            await message.answer('Выберите сегодняшнюю или будущую дату.');return
        await state.set_state(target)
        await message.answer('Выбрано: '+dt.strftime('%d.%m.%Y'))
        await self.n[DATE_HANDLERS[target]](message.model_copy(update={'text':dt.strftime('%d.%m.%Y')}),state)

    async def submit(self,c,state,bot):
        if await state.get_state()!='UIFlow:review':return
        data=await state.get_data();uid=c.from_user.id
        if '_edit_table' in data:
            table=data['_edit_table'];item=data['_edit_id'];key='request_id' if table=='requests' else 'user_id'
            row=self.one(f'SELECT * FROM {table} WHERE {key}=?',(item,))
            if not row or (row.get('business_user_id') if table=='requests' else row['user_id'])!=uid:
                await c.message.answer('Запись недоступна.');return
            allowed=self.allowed_fields(table,row);values=data['_edit_values']
            fields=[k for k in allowed if k in values]
            sql=f"UPDATE {table} SET "+','.join(k+'=?' for k in fields)+",status='pending',moderated_at=NULL,moderated_by=NULL WHERE "+key+'=?'
            self.execute(sql,tuple(values.get(k) for k in fields)+(item,))
            self.execute('DELETE FROM ui_notes WHERE kind=? AND item_id=?',(table,item))
            await state.clear()
            await c.message.answer('Изменения отправлены на модерацию. Остальные данные сохранены.',reply_markup=self.n['business_menu_kb']() if table in {'requests','business_profiles'} else self.n['creator_menu_kb']())
            return
        name=data.get('_submit')
        if name not in {v[0] for v in FINAL.values()}:return
        await state.update_data(_confirmed=True)
        await state.set_state(data['_final_state'])
        message=c.message.model_copy(update={'from_user':c.from_user,'text':data['_answer']})
        handler=self.n[name]
        await handler(message,state,**({'bot':bot} if 'bot' in inspect.signature(handler).parameters else {}))

    def pagination(self,prefix,page,total,size=8,extra=None):
        rows=list(extra or [])
        controls=[]
        if page:controls.append(button('← Назад',f'{prefix}:{page-1}'))
        if (page+1)*size<total:controls.append(button('Далее →',f'{prefix}:{page+1}'))
        if controls:rows.append(controls)
        rows.append([button('🏠 В меню','ui:home')])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def page(self,rows,page,size=8):
        page=max(0,min(page,max(0,(len(rows)-1)//size)))
        return rows[page*size:(page+1)*size],page

    def project_status(self,r):
        if r['status']=='active':
            return 'Приём откликов открыт' if self.n['project_distribution_open'](r) else 'Приём откликов закрыт'
        return STATUS.get(r['status'],r['status'])

    def short_card(self,r):
        task=(r.get('task') or 'Описание проекта').strip()
        brief=task if len(task)<=180 else task[:177]+'…'
        terms=r.get('cooperation_format') or 'Обсуждается'
        budget=r.get('budget') or 'Обсуждается'
        if 'бартер' in terms.lower() and str(budget).strip() in {'0','0 ₽','0 руб','0 руб.'}:
            budget='Товар / услуга по условиям проекта'
        return f"<b>#{r['request_id']} · {clean(r['business_name'])}</b>\n{clean(r.get('creator_needed'))}\n📍 {clean(r.get('work_format'))} · {clean(r.get('city'))}\n\n{clean(brief)}\n\n<b>Условия:</b> {clean(terms)}\n<b>Бюджет:</b> {clean(budget)}\n<b>Отклики до:</b> {clean(r.get('response_deadline'))}\n<b>{self.project_status(r)}</b>"

    async def browse_projects(self,c,section='current',page=0,creator=False):
        uid=c.from_user.id
        if creator:
            profile=self.one('SELECT * FROM creators WHERE user_id=?',(uid,))
            if not profile or profile['status']!='active':
                await c.message.answer('Проекты откроются после одобрения профиля.',reply_markup=keyboard(('Мой профиль','creator_profile'),('🏠 В меню','ui:home')));return
            rows=self.rows("SELECT * FROM requests WHERE status='active' AND creator_needed IN (?,?) ORDER BY request_id DESC",(profile['creator_type'],self.n['BOTH_CREATORS']))
            rows=[r for r in rows if self.n['project_distribution_open'](r)]
            prefix='ui:catalog';title='Проекты для вас';extra=[]
        else:
            rows=self.rows('SELECT * FROM requests WHERE business_user_id=? ORDER BY request_id DESC',(uid,))
            buckets={'current':[],'pending':[],'archive':[]}
            for r in rows:
                buckets['pending' if r['status'] in {'pending','rejected'} else 'current' if self.n['project_distribution_open'](r) else 'archive'].append(r)
            section=section if section in buckets else 'current';rows=buckets[section]
            prefix=f'ui:projects:{section}';title={'current':'Текущие проекты','pending':'На модерации / исправлении','archive':'Архив'}[section]
            extra=[[button(f'{label} ({len(buckets[k])})',f'ui:projects:{k}:0')] for k,label in [('current','Текущие'),('pending','На модерации'),('archive','Архив')]]
            extra.append([button('➕ Создать проект','business_create_request','success')])
        subset,page=self.page(rows,page)
        await c.message.answer(f'<b>{title}</b>\n'+(f'Всего: {len(rows)} · Страница {page+1}' if rows else 'Пока здесь нет проектов.'),reply_markup=self.pagination(prefix,page,len(rows),extra=extra))
        for r in subset:
            rid=r['request_id'];actions=[('Подробнее',f'ui:detail:{rid}')]
            if creator:
                sent=self.one('SELECT response_id FROM responses WHERE request_id=? AND creator_user_id=?',(rid,uid))
                actions.append(('✅ Отклик отправлен','ui:sent') if sent else ('Откликнуться',f'respond:{rid}','success'))
            else:
                new=self.one("SELECT COUNT(*) AS n FROM responses WHERE request_id=? AND status='new'",(rid,))['n']
                actions.append((f'Отклики · новых {new}',f'ui:responses:{rid}:0'))
                if r['status'] in {'pending','rejected'}:actions.append(('Исправить',f'ui:correct:{rid}'))
                if r['status']=='active':actions.append(('Завершить проект',f'complete_project:{rid}'))
                if r['status']=='completed':actions.append(('Итоги',f'project_summary:{rid}'))
            actions.append(('← К проектам','creator_view_requests' if creator else 'business_my_requests'))
            await c.message.answer(self.short_card(r),reply_markup=keyboard(*actions))

    async def detail(self,c,rid):
        uid=c.from_user.id;r=self.one('SELECT * FROM requests WHERE request_id=?',(rid,))
        if not r:return
        owned=r['business_user_id']==uid
        profile=self.one('SELECT * FROM creators WHERE user_id=?',(uid,))
        if not owned and not self.n['is_admin'](uid) and (not profile or profile['status']!='active' or not self.n['request_accepts_creator'](r,profile['creator_type']) or not self.n['project_distribution_open'](r)):
            await c.message.answer('Этот проект сейчас недоступен.');return
        actions=[]
        if owned:
            actions.append(('Отклики проекта',f'ui:responses:{rid}:0'))
        elif profile and self.n['project_distribution_open'](r):
            sent=self.one('SELECT response_id FROM responses WHERE request_id=? AND creator_user_id=?',(rid,uid))
            actions.append(('✅ Отклик отправлен','ui:sent') if sent else ('Откликнуться',f'respond:{rid}','success'))
        if owned and r['status']=='rejected':actions.append(('Исправить',f'ui:correct:{rid}'))
        actions.append(('← К проектам','business_my_requests' if owned else 'creator_view_requests'))
        actions.append(('🏠 В меню','ui:home'))
        note=self.one('SELECT reason FROM ui_notes WHERE kind=? AND item_id=?',('requests',rid))
        prefix='<b>'+self.project_status(r)+'</b>'
        if note and r['status']=='rejected':prefix+='\nЧто исправить: '+clean(note['reason'])
        await self.n['send_request_card'](c.message,r,reply_markup=keyboard(*actions),prefix=prefix)

    async def responses(self,c,rid=0,page=0,mine=False):
        uid=c.from_user.id
        if rid and not self.one('SELECT request_id FROM requests WHERE request_id=? AND business_user_id=?',(rid,uid)):
            await c.message.answer('Проект недоступен.');return
        clause='r.creator_user_id=?' if mine else 'r.business_user_id=?'
        params=[uid]
        if rid:clause+=' AND r.request_id=?';params.append(rid)
        rows=self.rows('SELECT r.*,p.business_name,p.task,c.name,c.social_link FROM responses r JOIN requests p ON p.request_id=r.request_id JOIN creators c ON c.user_id=r.creator_user_id WHERE '+clause+' ORDER BY r.response_id DESC',tuple(params))
        subset,page=self.page(rows,page)
        prefix='ui:myresponses' if mine else f'ui:responses:{rid}'
        await c.message.answer(('<b>Мои отклики</b>' if mine else f'<b>Отклики{(" проекта #"+str(rid)) if rid else ""}</b>')+f'\nВсего: {len(rows)}',reply_markup=self.pagination(prefix,page,len(rows)))
        for r in subset:
            text=f"<b>#{r['request_id']} · {clean(r['business_name'])}</b>\n{clean((r['task'] or '')[:180])}\n{'' if mine else 'Креатор: '+clean(r['name'])+chr(10)}Статус: {STATUS.get(r['status'],r['status'])}"
            actions=[]
            if not mine:
                actions.append(('Анкета',f"ui:responseprofile:{r['response_id']}"))
                if r['status']=='new':actions += [('Принять',f"accept:{r['response_id']}",'success'),('Отклонить',f"decline:{r['response_id']}")]
            if r['status']=='accepted':actions.append(('Контакты',f"ui:contacts:{r['response_id']}"))
            actions.append(('🏠 В меню','ui:home'))
            await c.message.answer(text,reply_markup=keyboard(*actions))

    async def profile(self,c,business=False):
        uid=c.from_user.id;table='business_profiles' if business else 'creators'
        row=self.one(f'SELECT * FROM {table} WHERE user_id=?',(uid,))
        if not row:
            await c.message.answer('Профиль пока не заполнен.',reply_markup=self.n['business_intro_kb']() if business else self.n['creator_intro_kb']());return
        title='<b>Мой профиль</b>\nСтатус: '+STATUS.get(row['status'],'На проверке')
        note=self.one('SELECT reason FROM ui_notes WHERE kind=? AND item_id=?',(table,uid))
        if row['status']=='rejected':title+='\nЧто исправить: '+clean(note['reason'] if note else 'Уточните данные анкеты. За подробностями можно обратиться в поддержку.')
        actions=keyboard(('Изменить профиль','business_edit_profile' if business else 'creator_edit_profile'),('❓ Помощь','support_start'),('🏠 В меню','ui:home'))
        if business:
            await c.message.answer(title+'\n\n'+'\n'.join(f'{LABELS[k]}: {clean(row[k])}' for k in self.allowed_fields(table,row)),reply_markup=actions)
        else:
            await c.message.answer(title)
            await self.n['send_creator_card'](c.message,row,reply_markup=actions)

    async def reject_start(self,c,state):
        if not self.n['is_admin'](c.from_user.id):return
        parts=c.data.split(':')
        if parts[0]=='profile_reject':
            kind=parts[1];item=int(parts[2]);table='business_profiles' if kind=='business' else 'creators'
            if kind not in {'business','blogger','content'}:return
            row=self.one(f'SELECT * FROM {table} WHERE user_id=?',(item,))
            if row and table=='creators' and row['creator_type']!=('Блогер' if kind=='blogger' else 'Контент-креатор'):return
        else:
            table='requests';item=int(parts[1]);row=self.one('SELECT * FROM requests WHERE request_id=?',(item,));kind='projects'
        if not row or row['status']!='pending':
            await c.message.answer('Запись уже обработана.');return
        await state.clear();await state.set_state('UIFlow:reason')
        await state.update_data(_reject_table=table,_reject_id=item,_reject_kind=kind)
        await c.message.answer('Что нужно исправить? Выберите причину или напишите своё пояснение.',reply_markup=keyboard(
            ('Не хватает информации','ui:reason:0'),('Проверьте ссылки и контакты','ui:reason:1'),('Уточните задачу и условия','ui:reason:2'),('Отмена','ui:home')))

    async def reject(self,message,uid,state,bot,reason):
        data=await state.get_data()
        if not self.n['is_admin'](uid) or await state.get_state()!='UIFlow:reason':return
        if not reason.strip():return
        table=data['_reject_table'];item=data['_reject_id'];key='request_id' if table=='requests' else 'user_id'
        row=self.one(f'SELECT * FROM {table} WHERE {key}=?',(item,))
        if not row or row['status']!='pending':
            await state.clear();await message.answer('Запись уже обработана.');return
        changed=self.execute(f"UPDATE {table} SET status='rejected',moderated_at=?,moderated_by=? WHERE {key}=? AND status='pending'",(self.n['datetime'].now(self.n['MSK_TZ']).isoformat(),uid,item))
        if not changed:await state.clear();return
        self.execute('INSERT INTO ui_notes(kind,item_id,reason) VALUES (?,?,?) ON CONFLICT(kind,item_id) DO UPDATE SET reason=excluded.reason',(table,item,reason))
        target=row['business_user_id'] if table=='requests' else item
        action=f'ui:correct:{item}' if table=='requests' else 'business_edit_profile' if table=='business_profiles' else 'creator_edit_profile'
        try:
            await bot.send_message(target,'<b>Нужны исправления</b>\n\n'+clean(reason)+'\n\nИсправьте данные и отправьте их на повторную проверку.',reply_markup=keyboard(('Исправить',action)))
        except Exception:
            self.n['logger'].exception('Moderation reason notification failed: %s',item)
        await state.clear();await message.answer('Возвращено на доработку. Причина сохранена.')
        if table=='requests':await self.n['show_next_project_for_moderation'](message)
        else:await self.n['show_next_profile_for_moderation'](message,data['_reject_kind'])

    async def directory(self,c,kind,page=0):
        if not self.n['is_admin'](c.from_user.id):return
        if kind in {'bloggers','content','all'}:
            where=' WHERE creator_type=?' if kind!='all' else ''
            params=(('Блогер' if kind=='bloggers' else 'Контент-креатор'),) if kind!='all' else ()
            rows=self.rows('SELECT * FROM creators'+where+' ORDER BY user_id DESC',params)
        elif kind=='business':rows=self.rows('SELECT * FROM business_profiles ORDER BY user_id DESC')
        elif kind=='manual':rows=self.rows('SELECT * FROM admin_businesses ORDER BY business_id DESC')
        else:return
        subset,page=self.page(rows,page)
        await c.message.answer(f'<b>База · {len(rows)}</b>\nСтраница {page+1}',reply_markup=self.pagination(f'ui:directory:{kind}',page,len(rows)))
        for row in subset:
            if kind in {'bloggers','content','all'}:await self.n['send_creator_card'](c.message,row,include_contact=True)
            else:
                await c.message.answer('\n'.join(f'{LABELS[k]}: {clean(row.get(k))}' for k in ['business_name','city','niche','social_link','contact']),reply_markup=self.n['admin_business_card_kb'](row['business_id']) if kind=='manual' else None)

    async def admin_message(self, message):
        await self.admin_listing(message,message.from_user.id,'responses' if message.text=='💬 Все отклики' else 'referrals')

    async def admin_listing(self,message,uid,kind,page=0,item=0):
        if not self.n['is_admin'](uid):return
        if kind=='responses':
            rows=self.rows('SELECT r.*,c.name,p.business_name,p.task FROM responses r JOIN creators c ON c.user_id=r.creator_user_id JOIN requests p ON p.request_id=r.request_id'+(' WHERE r.request_id=?' if item else '')+' ORDER BY r.response_id DESC',(item,) if item else ())
            title='Отклики'+(f' проекта #{item}' if item else '')
        elif kind in {'new','closed'}:
            rows=self.rows('SELECT t.*, (SELECT m.message_text FROM support_messages m WHERE m.ticket_id=t.ticket_id ORDER BY m.message_id LIMIT 1) AS question FROM support_tickets t WHERE t.status=? ORDER BY t.ticket_id DESC',(kind,))
            title='Новые вопросы' if kind=='new' else 'Закрытые вопросы'
        elif kind=='referrals':
            rows=self.rows('SELECT u.user_id,u.full_name,u.username,COUNT(i.user_id) AS total FROM users u JOIN users i ON i.referred_by=u.user_id GROUP BY u.user_id,u.full_name,u.username ORDER BY total DESC,u.user_id')
            title='Приглашения'
        elif kind=='choose':
            rows=self.rows('SELECT * FROM admin_businesses ORDER BY business_name,business_id')
            title='Выберите бизнес'
        else:return
        subset,page=self.page(rows,page)
        await message.answer(f'<b>{title}</b>\nВсего: {len(rows)} · Страница {page+1}',reply_markup=self.pagination(f'ui:adminlist:{kind}:{item}',page,len(rows)))
        for row in subset:
            if kind=='responses':
                await message.answer(f"<b>Отклик #{row['response_id']}</b> · {STATUS.get(row['status'],row['status'])}\nБизнес: {clean(row['business_name'])}\nКреатор: {clean(row['name'])}\nПроект: {clean(row['task'])}",reply_markup=keyboard(('Отклики проекта',f"owner_project_responses:{row['request_id']}")))
            elif kind in {'new','closed'}:
                await message.answer(f"<b>Вопрос #{row['ticket_id']}</b> · {clean(row['role'])}\n{clean(row['question'])}",reply_markup=self.n['support_ticket_kb'](row['ticket_id']))
            elif kind=='choose':
                await message.answer(clean(row['business_name']),reply_markup=keyboard(('Создать проект',f"admin_request_for:{row['business_id']}")))
            else:
                await message.answer(f"{clean(row['full_name'])} · {clean(row['username'])}\nПриглашено: {row['total']}")

    async def callback(self,callback,state:FSMContext,bot):
        c=callback;uid=c.from_user.id;code=c.data;parts=code.split(':')
        # Every branch acknowledges once; message operations do not rely on the sender of bot messages.
        await c.answer()
        if code in {'ui:noop','ui:sent'}:return
        if code in {'ui:home','ui:save'}:
            await self.home(c.message,uid,state);return
        if code=='ui:resume':
            saved=self.one('SELECT payload FROM ui_drafts WHERE user_id=?',(uid,))
            if not saved:await c.message.answer('Нет сохранённого черновика.');return
            payload=json.loads(saved['payload']);await state.set_state(payload['state']);await state.set_data(payload['data'])
            data=payload['data'];current=payload['state']
            if current=='UIFlow:review':await self.review(c.message,state)
            elif current=='UIFlow:fields':await self.edit_fields(c.message,state)
            elif current=='UIFlow:field':await self.field_keyboard(c.message,state)
            elif current=='UIFlow:date':await self.calendar(c.message,state)
            elif current=='UIFlow:reason':await c.message.answer('Напишите причину возврата на доработку.')
            else:
                history=data.get('_form_history',[])
                if history:
                    prompt=history[-1];await c.message.answer(prompt['text'],reply_markup=InlineKeyboardMarkup.model_validate(prompt['markup']) if prompt['markup'] else None)
                else:await c.message.answer('Продолжите заполнение с последнего вопроса.')
            return
        if code=='ui:restart':
            saved=self.one('SELECT payload FROM ui_drafts WHERE user_id=?',(uid,));payload=json.loads(saved['payload']) if saved else {}
            group=(payload.get('state') or '').split(':')[0];data=payload.get('data',{})
            group=(data.get('_final_state') or '').split(':')[0] if group=='UIFlow' else group
            self.execute('DELETE FROM ui_drafts WHERE user_id=?',(uid,));await state.clear()
            entry={'CreatorForm':'role_creator','ContentCreatorForm':'content_creator_start','BusinessForm':'role_business','RequestForm':'create_request_start'}.get(group)
            if entry:await self.n[entry](c,state)
            else:await self.home(c.message,uid,state)
            return
        if code=='ui:submit':await self.submit(c,state,bot);return
        if code in {'ui:editreview','ui:review'}:
            if await state.get_state() not in {'UIFlow:review','UIFlow:fields','UIFlow:field'}:return
            await (self.edit_fields(c.message,state) if code.endswith('editreview') else self.review(c.message,state));return
        if code.startswith('ui:field:'):
            if await state.get_state()!='UIFlow:fields':return
            await self.field_prompt(c.message,state,int(parts[2]));return
        if code.startswith('ui:option:'):
            if await state.get_state()!='UIFlow:field':return
            data=await state.get_data();idx=int(parts[2])
            if idx>=len(data.get('_options',[])):return
            value=data['_options'][idx]
            if data['_multi']:
                selected=data['_selected'];selected.remove(value) if value in selected else selected.append(value)
                await state.update_data(_selected=selected);await self.field_keyboard(c.message,state)
            else:await self.set_field(c.message,state,value)
            return
        if code=='ui:optiondone':
            if await state.get_state()!='UIFlow:field':return
            selected=(await state.get_data()).get('_selected',[])
            if selected:await self.set_field(c.message,state,', '.join(selected))
            else:await c.message.answer('Выберите хотя бы один вариант.')
            return
        if code=='ui:fieldskip':
            if await state.get_state()!='UIFlow:field':return
            field=(await state.get_data())['_field']
            value='Неважно' if field.startswith('blogger_') or field=='project_geography' else 'Обсуждается' if field in {'budget','price'} else 'Нет требований' if field=='requirements' else None
            await self.set_field(c.message,state,value);return
        if code.startswith('ui:date:'):
            message=c.message.model_copy(update={'from_user':c.from_user})
            await self.apply_date(message,state,(self.today()+timedelta(days=int(parts[2]))).strftime('%d.%m.%Y'));return
        if code=='ui:calendar':await self.calendar(c.message,state);return
        if code.startswith('ui:month:'):await self.calendar(c.message,state,int(parts[2]),int(parts[3]));return
        if code.startswith('ui:pick:'):
            await self.apply_date(c.message.model_copy(update={'from_user':c.from_user}),state,parts[2]);return
        if code.startswith('ui:adminlist:'):
            await self.admin_listing(c.message,uid,parts[2],int(parts[4]),int(parts[3]));return
        if code.startswith('support_list:'):
            await self.admin_listing(c.message,uid,parts[1]);return
        if code.startswith('owner_project_responses:'):
            await self.admin_listing(c.message,uid,'responses',item=int(parts[1]));return
        if code=='admin_request_choose_business':
            await self.admin_listing(c.message,uid,'choose');return
        if code in {'business_edit_profile','creator_edit_profile'}:
            await self.start_edit(c.message,uid,state,'business_profiles' if code.startswith('business') else 'creators');return
        if code.startswith('ui:correct:'):await self.start_edit(c.message,uid,state,'requests',int(parts[2]));return
        if code in {'business_profile','creator_profile'}:await self.profile(c,code.startswith('business'));return
        if code=='creator_view_requests':await self.browse_projects(c,creator=True);return
        if code.startswith('ui:catalog:'):await self.browse_projects(c,page=int(parts[2]),creator=True);return
        if code=='business_my_requests':await self.browse_projects(c);return
        if code.startswith('ui:projects:'):await self.browse_projects(c,parts[2],int(parts[3]));return
        if code=='business_responses':await self.responses(c);return
        if code.startswith('ui:responses:'):await self.responses(c,int(parts[2]),int(parts[3]));return
        if code=='creator_my_responses':await self.responses(c,mine=True);return
        if code.startswith('ui:myresponses:'):await self.responses(c,page=int(parts[2]),mine=True);return
        if code.startswith('ui:detail:'):await self.detail(c,int(parts[2]));return
        if code.startswith(('ui:contacts:','ui:responseprofile:')):
            r=self.one('SELECT * FROM responses WHERE response_id=?',(int(parts[2]),))
            if not r or uid not in {r['creator_user_id'],r['business_user_id']}:return
            if parts[1]=='responseprofile':
                if uid!=r['business_user_id']:return
                profile=self.one('SELECT * FROM creators WHERE user_id=?',(r['creator_user_id'],))
                if profile:await self.n['send_creator_card'](c.message,profile,reply_markup=keyboard(('← К откликам',f"ui:responses:{r['request_id']}:0")))
            elif r['status']=='accepted':
                if uid==r['business_user_id']:
                    profile=self.one('SELECT * FROM creators WHERE user_id=?',(r['creator_user_id'],));target=r['creator_user_id']
                else:
                    profile=self.one('SELECT * FROM requests WHERE request_id=?',(r['request_id'],));target=r['business_user_id']
                await c.message.answer(self.n['contact_line'](target,profile.get('contact') if profile else None),reply_markup=keyboard(('🏠 В меню','ui:home')))
            return
        if code.startswith(('profile_reject:','moderate_reject:')):await self.reject_start(c,state);return
        if code.startswith('ui:reason:'):
            reasons=['Не хватает информации. Дополните описание и данные анкеты.','Проверьте ссылки и контакт: они должны вести на доступные страницы и действующий способ связи.','Уточните задачу, объём работы и условия сотрудничества.']
            await self.reject(c.message,uid,state,bot,reasons[int(parts[2])]);return
        if code.startswith('media_base:'):await self.directory(c,parts[1]);return
        if code.startswith('ui:directory:'):await self.directory(c,parts[2],int(parts[3]));return
        if code in {'registered_businesses','admin_business_list'}:await self.directory(c,'business' if code=='registered_businesses' else 'manual');return
        if code=='creator_settings' or code.startswith('ui:notifications'):
            pref=self.one('SELECT projects FROM ui_preferences WHERE user_id=?',(uid,));enabled=pref['projects'] if pref else 1
            if code=='ui:notifications:toggle':
                enabled=1-enabled;self.execute('INSERT INTO ui_preferences(user_id,projects) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET projects=excluded.projects',(uid,enabled))
            await c.message.answer('Уведомления о новых проектах: '+('включены' if enabled else 'выключены')+'.\nОтветы бизнеса и сообщения поддержки приходят всегда.',reply_markup=keyboard(('Выключить' if enabled else 'Включить','ui:notifications:toggle'),('🏠 В меню','ui:home')))

    async def message(self,message,state:FSMContext,bot):
        current=await state.get_state()
        if current=='UIFlow:reason':await self.reject(message,message.from_user.id,state,bot,message.text or '');return
        if current=='UIFlow:date':await self.apply_date(message,state,message.text or '');return
        if current=='UIFlow:field':
            data=await state.get_data()
            if data['_field']=='photo_file_id':
                if not message.photo:await message.answer('Пришлите одно фото.');return
                value=message.photo[-1].file_id
            else:
                if not message.text:await message.answer('На этом шаге нужен текст.');return
                value=message.text.strip()
            await self.set_field(message,state,value);return
        await message.answer('Выберите действие кнопкой под последним сообщением.')

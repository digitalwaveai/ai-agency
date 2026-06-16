import requests
import streamlit as st

API = st.sidebar.text_input("API URL", "http://localhost:8000")
st.title("Beauty Lead Finder Assistant")
with st.form("search"):
    niche = st.text_input("Ниша", "косметолог")
    city = st.text_input("Город", "Москва")
    country = st.text_input("Страна", "Россия")
    services = st.multiselect("Услуги", ["сайт", "Telegram-бот", "автоматизация записи", "воронка", "упаковка соцсетей", "запуск продукта"], ["сайт", "Telegram-бот"])
    limit = st.number_input("Сколько лидов", 1, 100, 10)
    min_score = st.slider("Минимальный score", 0, 100, 0)
    contacts_only = st.checkbox("Только с контактами")
    exclude = st.text_input("Кого исключать", "крупные сети франшизы агентства")
    if st.form_submit_button("Найти / обновить список"):
        r = requests.post(f"{API}/search", json=locals() | {"target_type":"частные эксперты", "language":"ru"}, timeout=60)
        st.write(r.json())

st.subheader("Лиды")
filters = {"niche": st.text_input("Фильтр ниша"), "city": st.text_input("Фильтр город"), "min_score": st.slider("Score от", 0, 100, 0, key="fscore"), "status": st.selectbox("Статус", ["", "new", "qualified", "contacted", "replied", "not_fit", "do_not_contact", "archived"])}
leads = requests.get(f"{API}/leads", params={k:v for k,v in filters.items() if v not in [""]}, timeout=30).json()
st.dataframe(leads)
lead_id = st.number_input("ID лида для карточки", min_value=1, step=1)
if st.button("Открыть карточку"):
    lead = requests.get(f"{API}/leads/{lead_id}").json(); st.json(lead)
    st.session_state["lead"] = lead
if st.button("Сгенерировать сообщение"):
    st.json(requests.post(f"{API}/leads/{lead_id}/outreach").json())
with st.form("update"):
    status = st.selectbox("Новый статус", ["new", "qualified", "contacted", "replied", "not_fit", "do_not_contact", "archived"])
    notes = st.text_area("Заметки")
    if st.form_submit_button("Сохранить статус/заметки"):
        st.json(requests.patch(f"{API}/leads/{lead_id}", json={"status":status,"notes":notes}).json())
st.download_button("Экспорт CSV", data=requests.get(f"{API}/export.csv").text, file_name="leads.csv")

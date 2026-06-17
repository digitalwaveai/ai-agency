import requests
import streamlit as st

API = st.sidebar.text_input("API URL", "http://localhost:8000")
st.title("Beauty Lead Finder Assistant")


def api_request(method: str, path: str, **kwargs):
    try:
        response = requests.request(method, f"{API}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        return response.text if "text/csv" in content_type else response.json()
    except Exception as exc:
        st.warning(f"Backend недоступен или вернул ошибку: {exc}")
        return None

=======

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

        payload = {"niche": niche, "city": city, "country": country, "services": services, "limit": limit, "min_score": min_score, "contacts_only": contacts_only, "exclude": exclude, "target_type": "частные эксперты", "language": "ru"}
        result = api_request("POST", "/search", json=payload, timeout=60)
        if result is not None:
            st.write(result)

st.subheader("Лиды")
filters = {"niche": st.text_input("Фильтр ниша"), "city": st.text_input("Фильтр город"), "min_score": st.slider("Score от", 0, 100, 0, key="fscore"), "status": st.selectbox("Статус", ["", "new", "qualified", "contacted", "replied", "not_fit", "do_not_contact", "archived"])}
leads = api_request("GET", "/leads", params={k: v for k, v in filters.items() if v not in [""]}) or []
st.dataframe(leads)
lead_id = st.number_input("ID лида для карточки", min_value=1, step=1)
if st.button("Открыть карточку"):
    lead = api_request("GET", f"/leads/{lead_id}")
    if lead is not None:
        st.json(lead)
        st.session_state["lead"] = lead
if st.button("Сгенерировать сообщение"):
    result = api_request("POST", f"/leads/{lead_id}/outreach")
    if result is not None:
        st.json(result)

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
 
        result = api_request("PATCH", f"/leads/{lead_id}", json={"status": status, "notes": notes})
        if result is not None:
            st.json(result)
csv_data = api_request("GET", "/export.csv") or ""
st.download_button("Экспорт CSV", data=csv_data, file_name="leads.csv")

        st.json(requests.patch(f"{API}/leads/{lead_id}", json={"status":status,"notes":notes}).json())
st.download_button("Экспорт CSV", data=requests.get(f"{API}/export.csv").text, file_name="leads.csv")


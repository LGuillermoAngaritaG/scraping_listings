"""Form that sends a property to the serving endpoint and shows the estimated rent."""

import os

import streamlit as st
from databricks.sdk import WorkspaceClient

ENDPOINT = os.environ["SERVING_ENDPOINT"]
PROPERTY_TYPES = ["apartamento", "apartaestudio", "casa", "oficina", "local", "bodega"]


def clean_neighborhood(text: str) -> str:
    """Same cleaning as the pipeline: lowercase, no accents."""
    return text.strip().lower().translate(str.maketrans("áéíóúüñ", "aeiouun"))


st.set_page_config(page_title="Bogota rent estimate")
st.title("Monthly rent estimate, Bogota")

with st.form("property"):
    property_type = st.selectbox("Property type", PROPERTY_TYPES)
    neighborhood = st.text_input("Neighborhood (barrio)", "Chico Norte")
    area = st.number_input("Area (m²)", min_value=10.0, max_value=2000.0, value=70.0)
    bedrooms = st.number_input("Bedrooms", min_value=0, max_value=10, value=2)
    bathrooms = st.number_input("Bathrooms", min_value=0, max_value=10, value=2)
    parking = st.number_input("Parking spots", min_value=0, max_value=10, value=1)
    estrato = st.selectbox("Estrato", [1, 2, 3, 4, 5, 6], index=3)
    age = st.number_input("Building age (years)", min_value=0.0, max_value=100.0, value=10.0)
    submitted = st.form_submit_button("Estimate")

if submitted:
    record = {
        "source": "metrocuadrado",  # the model also learned which site a listing came from
        "property_type": property_type,
        "neighborhood": clean_neighborhood(neighborhood),
        "area_m2": float(area),
        "bedrooms": float(bedrooms),
        "bathrooms": float(bathrooms),
        "parking": float(parking),
        "estrato": float(estrato),
        "age_years": float(age),
    }
    response = WorkspaceClient().serving_endpoints.query(name=ENDPOINT, dataframe_records=[record])
    st.metric("Estimated monthly rent", f"$ {response.predictions[0]:,.0f} COP")

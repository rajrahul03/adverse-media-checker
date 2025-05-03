# File: app/main.py

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import os
import json
import uuid
import pdfkit
from datetime import timedelta
from dotenv import load_dotenv
from openai import OpenAI
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions

load_dotenv()

app = FastAPI()

# Serve static files (UI)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
async def serve_ui():
    return FileResponse(os.path.join("app", "static", "index.html"))

# Load config from environment
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CB_USERNAME = os.getenv("CB_USERNAME")
CB_PASSWORD = os.getenv("CB_PASSWORD")
CB_HOST = os.getenv("CB_HOST")
CB_BUCKET = os.getenv("CB_BUCKET")

# Setup OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# Setup Couchbase
auth = PasswordAuthenticator(CB_USERNAME, CB_PASSWORD)
cluster = Cluster(CB_HOST, ClusterOptions(auth))
cluster.wait_until_ready(timedelta(seconds=10))
bucket = cluster.bucket(CB_BUCKET)
collection = bucket.default_collection()

class SearchRequest(BaseModel):
    query: str
    type: str  # person or company

@app.post("/verify")
async def verify_entity(req: SearchRequest):
    search_query = f"{req.query} Adverse Media OR Legal Issues OR Controversy"

    try:
        serp_response = requests.get(
            "https://serpapi.com/search",
            params={
                "q": search_query,
                "api_key": SERPAPI_KEY,
                "engine": "google",
                "num": 5
            }
        ).json()

        links = [item['link'] for item in serp_response.get("organic_results", [])]
        summary = run_llm_analysis(req.query, links)

        result_id = str(uuid.uuid4())
        result_data = {
            "id": result_id,
            "query": req.query,
            "type": req.type,
            "summary": summary,
            "sources": links
        }

        # Save to Couchbase
        collection.upsert(result_id, result_data)

        # Save each link as PDF and upload binary
        os.makedirs("/tmp/pdfs", exist_ok=True)
        for idx, url in enumerate(links):
            pdf_path = f"/tmp/pdfs/{result_id}_link{idx + 1}.pdf"
            try:
                pdfkit.from_url(url, pdf_path)
                with open(pdf_path, "rb") as f:
                    collection.binary().upsert(f"{result_id}_pdf_{idx + 1}", f.read())
            except Exception as err:
                print(f"[PDF ERROR] {url} -> {err}")

        return result_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

def run_llm_analysis(query, links):
    prompt = f"""
    Given the name or company \"{query}\", and the following links:
    {chr(10).join(links)}

    Check if there is any indication of adverse media coverage, controversies, legal issues, or any public concerns. 
    Return only a valid JSON like:
    {{
      "risk_level": "High",
      "reason": "Frequent legal issues and regulatory fines.",
      "summary": "Company has faced multiple lawsuits and sanctions."
    }}
    Do not include explanations or Markdown. Only output the JSON.
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a compliance and media analysis assistant."},
            {"role": "user", "content": prompt},
        ]
    )

    content = response.choices[0].message.content.strip()

    try:
        # Try to extract JSON even if surrounded by extra text
        first_brace = content.find('{')
        last_brace = content.rfind('}')
        if first_brace != -1 and last_brace != -1:
            content = content[first_brace:last_brace+1]
        parsed = json.loads(content)
    except Exception as e:
        parsed = {
            "risk_level": "Unknown",
            "reason": "Could not parse structured JSON from model.",
            "summary": content
        }

    return parsed


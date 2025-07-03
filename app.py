from shiny import App, ui, render, reactive, run_app
import pandas as pd
import psycopg2
import openai
import os
from dotenv import load_dotenv

# === Load environment variables ===
load_dotenv()

# === Debug: Print DB Host for verification ===
print("DB HOST:", os.getenv("DB_HOST"))

# === Validate OpenAI key ===
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY is not set in environment.")
openai.api_key = openai_api_key

# === PostgreSQL credentials from environment ===
try:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432")
    )
except Exception as e:
    raise RuntimeError(f"Error connecting to PostgreSQL: {e}")

cursor = conn.cursor()

# === Get list of tables ===
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
    ORDER BY table_name;
""")
tables = [row[0] for row in cursor.fetchall()]

# === Generate schema string ===
cursor.execute("""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
    ORDER BY table_name, ordinal_position;
""")
schema_info = cursor.fetchall()
schema_str = "\n".join(f"{table}: {column} ({dtype})" for table, column, dtype in schema_info)

# === Prompt template ===
def construct_prompt(nl_query, schema):
    return f"""
You are an expert SQL assistant. Given the database schema and a natural language question,
write an optimized PostgreSQL SELECT query using proper table joins if needed.

Schema:
{schema}

Question:
{nl_query}

SQL:
"""

# === UI ===
app_ui = ui.page_fluid(
    ui.h2("Natural Language to SQL using GPT-4 and PostgreSQL"),
    ui.input_select("table_select", "Preview a table:", choices=tables),
    ui.output_table("table_preview"),
    ui.hr(),
    ui.input_text("nl_input", "Natural Language Query:"),
    ui.input_action_button("generate_sql", "Generate SQL"),
    ui.input_text_area("sql_input", "SQL Query (editable):", rows=4),
    ui.input_action_button("run_query", "Run SQL"),
    ui.output_table("query_results")
)

# === Server ===
def server(input, output, session):

    @output
    @render.table
    def table_preview():
        selected = input.table_select()
        if not selected:
            return pd.DataFrame()
        try:
            return pd.read_sql_query(f"SELECT * FROM public.{selected} LIMIT 10", conn)
        except Exception as e:
            return pd.DataFrame({"Error": [str(e)]})

    @reactive.effect
    @reactive.event(input.generate_sql)
    def generate_sql():
        nl_query = input.nl_input()
        if not nl_query:
            return
        try:
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert in translating natural language to SQL."},
                    {"role": "user", "content": construct_prompt(nl_query, schema_str)}
                ],
                temperature=0
            )
            sql = response.choices[0].message.content.strip()
            ui.update_text_area("sql_input", value=sql)
        except Exception as e:
            ui.update_text_area("sql_input", value=f"Error from OpenAI: {e}")

    @output
    @render.table
    @reactive.event(input.run_query)
    def query_results():
        sql = input.sql_input()
        if not sql.strip().lower().startswith("select"):
            return pd.DataFrame({"Error": ["Only SELECT queries are allowed"]})
        try:
            return pd.read_sql_query(sql, conn)
        except Exception as e:
            return pd.DataFrame({"Error": [str(e)]})

# === Run App (bind to 0.0.0.0 for Render) ===
app = App(app_ui, server)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    run_app(app, host="0.0.0.0", port=port)

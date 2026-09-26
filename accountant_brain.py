import ollama
from openai import OpenAI
from google import genai # New Import
from google.genai import types
import os
from dotenv import load_dotenv
from typing import List, Any # Add this to your imports

load_dotenv()


def ask_brain(pdf_text, model_choice):
    system_rules = "You are an expert Forensic Accountant. Extract data with 100% accuracy."

    user_prompt = f"""
        ### TASK: DATA EXTRACTION & YEAR INFERENCE
        You are extracting financial data from a credit card statement text.

        ### INSTRUCTIONS:
        1. Summary Table:
           Extract:
           - Statement Date
           - Opening Balance
           - Total Payments
           - Total Purchases
           - Total Interest
           - Ending Balance
        
        2. Transaction Table:
           Every line under ### TRANSACTIONS is already a valid transaction.
        
           For EVERY transaction line, output exactly one row with:
           - Statement Date
           - Transaction Date
           - Posting Date
           - Description
           - Amount
        
        3. Do not skip any transaction line.
        
        4. Do not combine two transaction lines.
        
        5. Do not remove a transaction because the same merchant appears more than once.
        
        6. Preserve the amount exactly, including the positive or negative sign.
        
        7. Year Inference:
           Infer the correct year for Transaction Date and Posting Date from the Statement Date.
        
           Example:
           If Statement Date is 2025-01-06:
           DEC 28 = 2024-12-28
           JAN 02 = 2025-01-02
        
        8. The number of transaction rows in your output MUST equal the number of lines under ### TRANSACTIONS.

        ### OUTPUT FORMAT:
        Provide exactly TWO CSV tables separated by a blank line. Do not include any other text.

        **Table 1: Summary**
        ```csv
        Statement Date,Opening Balance,Total Payments,Total Purchases,Total Interest,Ending Balance
        YYYY-MM-DD,0.00,0.00,0.00,0.00,0.00
        ```

        **Table 2: Transactions**
        ```csv
        Statement Date,Transaction Date,Posting Date,Description,Amount
        YYYY-MM-DD,YYYY-MM-DD,YYYY-MM-DD,Description Text,0.00
        ```

        ### DATA:
        {pdf_text}
        """


    if model_choice == "1":
        response = ollama.generate(model="deepseek-r1:8b",
                                   system=system_rules,
                                   prompt=user_prompt,
                                   options={"temperature": 0, "num_ctx": 8192}
                                   )
        return response['response']

    elif model_choice == "2":
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        messages_to_send: List[Any] = [
            {"role": "system", "content": system_rules},
            {"role": "user", "content": user_prompt}
        ]
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages_to_send,  # PyCharm is now happy
            temperature=0
        )
        return response.choices[0].message.content

    elif model_choice == "4":
        response = ollama.generate(
            model="qwen3:14b",
            system=system_rules,
            prompt=user_prompt,
            options={
                "temperature": 0,
                "num_ctx": 8192
            }
        )
        return response['response']
    elif model_choice == "3":
        print("Using Gemini via the new google-genai SDK...")

        # The client automatically looks for GOOGLE_API_KEY or GEMINI_API_KEY
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        response = client.models.generate_content(
            model="gemini-2.0-flash",  # Or your preferred model
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction="You are an expert Forensic Accountant.",
                temperature=0.0
            )
        )
        return response.text

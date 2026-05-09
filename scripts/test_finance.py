from vnstock import Vnstock
import json

stock = Vnstock().stock('FPT', 'VCI')

output = {}

try:
    ratio_df = stock.finance.ratio(period='year', lang='en')
    if ratio_df is not None and not ratio_df.empty:
        output['ratio_columns'] = ratio_df.columns.tolist()
except Exception as e:
    output['ratio_error'] = str(e)

try:
    inc_df = stock.finance.income_statement(period='year', lang='en')
    if inc_df is not None and not inc_df.empty:
        output['income_statement_columns'] = inc_df.columns.tolist()
except Exception as e:
    output['income_statement_error'] = str(e)

try:
    bal_df = stock.finance.balance_sheet(period='year', lang='en')
    if bal_df is not None and not bal_df.empty:
        output['balance_sheet_columns'] = bal_df.columns.tolist()
except Exception as e:
    output['balance_sheet_error'] = str(e)

try:
    cash_df = stock.finance.cash_flow(period='year', lang='en')
    if cash_df is not None and not cash_df.empty:
        output['cash_flow_columns'] = cash_df.columns.tolist()
except Exception as e:
    output['cash_flow_error'] = str(e)

with open('/tmp/finance_test.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("Test complete. Data written to /tmp/finance_test.json")

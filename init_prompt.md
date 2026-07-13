I need you to plan a script that:

First turn:

1. Using `FailsDB_SQLite_schema.json` reads <path_to_access_db> Access database table "FelTabell"
2. Creates SQLite database ("SQLite_Faults.db") <path_to_sqlite_db> with two tables : "FailsAndSolutions" and "Tracking"
- "FailsAndSolutions" has a fields:
	- "ID" (integer, key, autoincrement)
	- "equipment_type" (string)
	- "equipment_name" (string)
	- "internal_ID" (string)
	- "failure_description" (long string, text)
	- "solution_description" (long string, text)
	- "solution_description_cleaned" (long string, text)
	- "MD5" (hash, string)
- "Tracking" has fields:
	- "MD5" (hash, string)
	- "valid" (boolean)
	- "cleaned" (boolean)
    - "processed" (boolean)
3. Populates "SQLite_Faults.db" with data from "FelTabell", where:
- "FailsAndSolutions":
	- "equipment_type" = constant "Other"
	- "equipment_name" = constant "Other"
	- "internal_ID" = "Artnr" from "FelTabell"
	- "failure_description" = "Felbeskrivning" from "FelTabell"
	- "solution_description" = "Kommentar" from "FelTabell"
	- "solution_description_cleaned" = empty
	- "MD5" = MD5 hash of long sting of ("internal_ID"+"failure_description"+"solution_description")
- AND populates "Tracking"
	- "MD5" = "MD5" hash from "FailsAndSolutions" (to tie two tables together)
	- "valid", "cleaned" and "processed" if False
	
4. Logic of insertion : Insert only if "Felbeskrivning" is not empty AND 
- "Kommentar" is not empty AND 
- no equal "MD5" from "Tracking" exists (duplett elimination)
5. If "Artnr" is empty, "internal_ID" will be a string "Other"

Second pass:
1. Using "Tracking":"MD5" and Ollama's model (see reference) using "structured output" ask model if "FailsAndSolutions":"solution_description" is a valid technical solution for "FailsAndSolutions":"failure_description", (return boolean) , set "Tracking":"valid" to returned value (using REPLACE)
2. Ask modell for better description in case of "valid"=True, uppdate "FailsAndSolutions":"solution_description_cleaned" to description returned.
 Example prompt for model: 
 " 
 def build_prompt(failure: str, solution: str) -> str:
    """Construct the evaluation prompt in Swedish."""
    return f"""Du är en teknisk expert som granskar underhållsloggar.
Din uppgift är att bedöma om 'lösningen' faktiskt åtgärdar 'felet'.
Om det är en genuin teknisk lösning, skriv om den till en mer detaljerad och beskrivande version på svenska.
Om det bara är en statusnotering, lagerflytt, okänt fel, eller inte en verklig lösning, markera det som ogiltigt.

Felbeskrivning: {failure}
Lösning: {solution}

Svara endast med JSON enligt följande schema:
{{
  "is_valid": boolean,
  "enhanced_solution": "string eller null",
  "reasoning": "string"
}}"""
"

3. Repeat untill done.

Write process to sdtout, and use debug logging.
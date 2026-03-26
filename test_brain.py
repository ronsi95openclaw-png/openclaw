from core.brain import ask_llm

prompt = "BTC is down 5% today, RSI is 28. Should ClawBot DCA? Answer in one sentence."
response = ask_llm(prompt)
print(response)

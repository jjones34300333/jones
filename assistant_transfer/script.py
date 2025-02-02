from transformers import pipeline

# Sentiment Analysis Example
sentiment_analyzer = pipeline('sentiment-analysis')
text = "I love Termux!"
result = sentiment_analyzer(text)
print(result)


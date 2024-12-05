import requests
import schedule
import time
import logging
import openai
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Load environment variables from a .env file
from dotenv import load_dotenv
load_dotenv('credentials.env')

# Configure logging
logging.basicConfig(level=logging.DEBUG, filename='newsblur_telegram_bot.log', filemode='a',
                    format='%(asctime)s - %(levelname)s - %(message)s')
console = logging.StreamHandler()
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)


class NewsBlurTelegramBot:
    def __init__(self):
        # Environment Variables
        self.newsblur_api_url = 'https://www.newsblur.com'
        self.newsblur_user = os.getenv('NEWSBLUR_USER')
        self.newsblur_pass = os.getenv('NEWSBLUR_PASS')
        self.tele_token = os.getenv('TELE_TOKEN')
        self.tele_chat = os.getenv('TELE_CHAT')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        logging.debug(f"Loaded OpenAI API key: {self.openai_api_key}")
        logging.debug(f"NEWSBLUR_USER: {self.newsblur_user}")
        logging.debug(f"NEWSBLUR_PASS: {self.newsblur_pass}")
        logging.debug(f"TELE_TOKEN: {self.tele_token}")
        logging.debug(f"TELE_CHAT: {self.tele_chat}")
        if not self.openai_api_key:
            logging.error("No OpenAI API key provided. Please set the OPENAI_API_KEY environment variable.")
        if not self.tele_token or not self.tele_chat:
            logging.error("Telegram credentials missing. Please set the TELE_TOKEN and TELE_CHAT environment variables.")

        # Initialize OpenAI API key
        openai.api_key = self.openai_api_key

        # Create a single session with retry strategy
        self.session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def openai_request(self, model, system_message, user_message):
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message}
                ]
            )
            return response.choices[0].message['content'].strip()
        except openai.error.OpenAIError as e:
            logging.error(f"Error during OpenAI request: {e}")
            return None

    def fetch_newsblur_articles(self):
        """
        Logs into NewsBlur and fetches the latest articles.
        Returns:
            list: A list of articles fetched from NewsBlur.
        """
        logging.debug("Starting to fetch NewsBlur articles...")
        login_payload = {'username': self.newsblur_user, 'password': self.newsblur_pass}
        try:
            login_response = self.session.post(f'{self.newsblur_api_url}/api/login', data=login_payload)
            login_response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Failed to login to NewsBlur: {e}")
            return []

        logging.debug("Logged into NewsBlur successfully.")

        try:
            stories_response = self.session.get(f'{self.newsblur_api_url}/reader/river_stories')
            stories_response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Failed to fetch stories: {e}")
            return []

        logging.debug("Fetched stories successfully.")
        return stories_response.json().get('stories', [])

    def is_article_important(self, content):
        logging.debug("Checking if the article is important using GPT-4...")
        result = self.openai_request(
            model="gpt-4",
            system_message="You are a news assistant. You will be provided with the content of an article. Your task is to determine if the article contains critical or significant information that would be of interest to a general audience. Focus on breaking news, impactful events, major discoveries, or anything particularly insightful. Limit your response to only selecting highly important articles to ensure that no more than 4-5 highly relevant articles are shared at any time. Please respond with either 'important' or 'not important'.",
            user_message=f"Article content: {content}"
        )
        logging.debug(f"GPT-4 response for importance: {result}")
        return result == 'important' if result else False

    def summarize_article(self, content):
        logging.debug("Generating summary for the article using GPT-4...")
        summary = self.openai_request(
            model="gpt-4",
            system_message="You are a news assistant. Please provide a clear, concise, and engaging 2-3 sentence summary of the following article content, focusing on the key points and making it informative for a general audience.",
            user_message=f"Article content: {content}"
        )
        logging.debug(f"GPT-4 summary: {summary}")
        return summary if summary else "Summary could not be generated."

    def filter_important_articles(self, stories):
        logging.debug("Filtering important articles...")
        important_articles = [
            {
                'title': story.get('story_title'),
                'summary': self.summarize_article(story.get('story_content', '')),
                'url': story.get('story_permalink')
            }
            for story in stories
            if self.is_article_important(story.get('story_content', ''))
        ]  # Limit to 4 articles

        logging.debug(f"Found {len(important_articles)} important articles.")
        return important_articles

    def format_telegram_message(self, articles):
        if not articles:
            return None

        return "<b>📰 New Important Articles:</b>\n\n" + "".join(
            f"<b>🔗 Title:</b> <a href=\"{article['url']}\">{article['title']}</a>\n"
            f"<b>📝 Summary:</b> {article['summary']}\n\n"
            for article in articles
        )

    def send_telegram_message(self, articles):
        message = self.format_telegram_message(articles)
        if not message:
            logging.info("No important articles to send.")
            return

        url = f"https://api.telegram.org/bot{self.tele_token}/sendMessage"
        payload = {'chat_id': self.tele_chat, 'text': message, 'parse_mode': 'HTML'}
        try:
            response = self.session.post(url, data=payload)
            response.raise_for_status()
            logging.info("Message sent to Telegram successfully")
        except requests.RequestException as e:
            logging.error(f"Failed to send message to Telegram: {e}")
            logging.error(f"Payload used: {payload}")
            logging.error(f"Telegram API URL: {url}")
            logging.error("Make sure TELE_TOKEN and TELE_CHAT are correct and the bot has access to the specified chat.")

    def job(self):
        logging.debug("Job started.")
        articles = self.fetch_newsblur_articles()
        if not articles:
            logging.info("No new articles found.")
            return

        important_articles = self.filter_important_articles(articles)
        if important_articles:
            self.send_telegram_message(important_articles)
        else:
            logging.info("No important articles found.")

        logging.debug("Job completed.")

    def run(self):
        # Run the job immediately
        self.job()

        # Schedule the job every 60 minutes
        schedule.every(60).minutes.do(self.job)
        logging.info("Scheduler started. Waiting for the next job to run...")

        # Keep the script running
        while True:
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    bot = NewsBlurTelegramBot()
    bot.run()

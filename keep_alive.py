from flask import Flask, send_file
from threading import Thread
import io

app = Flask('')

@app.route('/')
def home():
    # This HTML includes the "Meta Tags" that Discord looks for
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SizzBedBot Status</title>
        <!-- The big text in the embed title -->
        <meta property="og:title" content="SizzBedBot is Online! 🟢" />
        
        <!-- The detailed description -->
        <meta property="og:description" content="The bot is currently running and monitoring for links. 
        Current Status: ALIVE and WATCHING 👀" />
        
        <!-- The color of the embed bar (Hex code for Green) -->
        <meta name="theme-color" content="#2ecc71">
        
        <!-- Optional: An image for the embed thumbnail -->
        <!-- You can replace this URL with any image link you want -->
        <meta property="og:image" content="https://i.imgur.com/4M34hi2.png" />
    </head>
    <body style="background-color: #2c2f33; color: white; font-family: sans-serif; text-align: center; padding-top: 50px;">
        <h1>🟢 SizzBedBot is Active</h1>
        <p>Do not close this tab if you are viewing this on a local host.</p>
        <p>Render is keeping me awake!</p>
    </body>
    </html>
    """

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

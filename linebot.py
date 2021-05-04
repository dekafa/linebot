from datetime import datetime, timezone, timedelta
import os
import re
import json
import requests
from flask import Flask, request, abort
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    FlexSendMessage,
    ImageMessage,
)
from imgur_python import Imgur
from PIL import Image, ImageDraw, ImageFont
import time

app = Flask(__name__)
#--------------------------------------------------------------------
# line
LINE_SECRET = os.getenv('LINE_SECRET')
LINE_TOKEN = os.getenv('LINE_TOKEN')
LINE_BOT = LineBotApi(LINE_TOKEN)
HANDLER = WebhookHandler(LINE_SECRET)

# azure電腦視覺金鑰及端點連線
SUBSCRIPTION_KEY = os.getenv('azurevis_key')
ENDPOINT = os.getenv('azurevis_endpoint')
CV_CLIENT = ComputerVisionClient(ENDPOINT, CognitiveServicesCredentials(SUBSCRIPTION_KEY))

# face驗證用戶
FACE_KEY = os.getenv('azureface_key')
FACE_END = os.getenv('azureface_endpoint')
FACE_CLIENT = FaceClient(FACE_END, CognitiveServicesCredentials(FACE_KEY))


#imgur
PERSON_GROUP_ID = "kafa"
IMGUR_CONFIG = {
  "client_id": os.getenv("client_id"),
  "client_secret": os.getenv("client_secret"),
  "access_token": os.getenv("access_token"),
  "refresh_token": os.getenv("refresh_token")
}
IMGUR_CLIENT = Imgur(config=IMGUR_CONFIG)

@app.route('/')
def index():
    return 'Hello World!'


#--------------------------------------------------------------------
# 物件辨識
def object_describe(url):
    description_results = CV_CLIENT.describe_image(url)
    output = ""
    for caption in description_results.captions:
        output += "'{}' with confidence {:.2f}% \n".format(
            caption.text, caption.confidence * 100
        )
    return output

#車牌辨識
def ocr_recognition(url):
    ocr_results = CV_CLIENT.read(url, raw=True)
    # Get the operation location (URL with an ID at the end) from the response
    operation_location_remote = ocr_results.headers["Operation-Location"]
    # Grab the ID from the URL
    operation_id = operation_location_remote.split("/")[-1]
    # Call the "GET" API and wait for it to retrieve the results
    while True:
        get_handw_text_results = CV_CLIENT.get_read_result(operation_id)
        if get_handw_text_results.status not in ["notStarted", "running"]:
            break
        time.sleep(1)
    # Get detected text
    text = []
    if get_handw_text_results.status == OperationStatusCodes.succeeded:
        for text_result in get_handw_text_results.analyze_result.read_results:
            for line in text_result.lines:
                if len(line.text) <= 8:
                    text.append(line.text)
    # Filter text for Taiwan license plate
    r = re.compile("[0-9A-Z]{2,4}[.-]{1}[0-9A-Z]{2,4}")
    text = list(filter(r.match, text))
    return text[0].replace(".", "-") if len(text) > 0 else ""


# 物件偵測
def object_detection(url, filename):
    img = Image.open(filename)
    draw = ImageDraw.Draw(img)
    font_size = int(5e-2 * img.size[1])
    fnt = ImageFont.truetype("微軟正黑體-1.ttf", size=font_size)
    object_detection = CV_CLIENT.detect_objects(url)
    if len(object_detection.objects) > 0:
        for obj in object_detection.objects:
            left = obj.rectangle.x
            top = obj.rectangle.y
            right = obj.rectangle.x + obj.rectangle.w
            bot = obj.rectangle.y + obj.rectangle.h
            name = obj.object_property
            confidence = obj.confidence
            print("{} at location {}, {}, {}, {}".format(name, left, right, top, bot))
            draw.rectangle([left, top, right, bot], outline=(255, 0, 0), width=3)
            draw.text(
                [left, top + font_size],
                "{} {}".format(name, confidence),
                fill=(255, 0, 0),
                font=fnt,
            )
    img.save(filename)
    image = IMGUR_CLIENT.image_upload(filename, "", "")
    link = image["response"]["data"]["link"]
    os.remove(filename)
    return link



# 人臉辨識
def face_recognition(filename):
    img = open(filename, "r+b")
    detected_face = FACE_CLIENT.face.detect_with_stream(
        img, detection_model="detection_01"
    )
    if len(detected_face) != 1:
        return ""
    results = FACE_CLIENT.face.identify([detected_face[0].face_id], PERSON_GROUP_ID)
    if len(results) == 0:
        return "unknown"
    result = results[0].as_dict()
    if len(result["candidates"]) == 0:
        return "unknown"
    if result["candidates"][0]["confidence"] < 0.5:
        return "unknown"
    person = FACE_CLIENT.person_group_person.get(
        PERSON_GROUP_ID, result["candidates"][0]["person_id"]
    )
    return person.name


#--------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    """
    LINE bot webhook callback
    """
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]
    print(signature)
    body = request.get_data(as_text=True)
    print(body)
    try:
        HANDLER.handle(body, signature)
    except InvalidSignatureError:
        print(
            "Invalid signature. Please check your channel access token/channel secret."
        )
        abort(400)
    return "OK"

# message 可以針對收到的訊息種類
# @HANDLER.add(MessageEvent, message=TextMessage)
# def handle_message(event):
#     url_dict = {
#       "TIBAME":"https://www.tibame.com/coursegoodjob/traffic_cli", 
#       "HELP":"https://developers.line.biz/zh-hant/docs/messaging-api/"}
# # 將要發出去的文字變成TextSendMessage
#     try:
#         url = url_dict[event.message.text.upper()]
#         message = TextSendMessage(text=url)
#     except:
#         message = TextSendMessage(text=event.message.text)
# # 回覆訊息
#     LINE_BOT.reply_message(event.reply_token, message)

@HANDLER.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    Reply text message
    """
    url_dict = {
        "TIBAME":"https://www.tibame.com/coursegoodjob/traffic_cli", 
        "HELP":"https://developers.line.biz/zh-hant/docs/messaging-api/"}

    try:
        url = url_dict[event.message.text.upper()]
        message = TextSendMessage(text=url)

    except:
        message = TextSendMessage(text=event.message.text)
    LINE_BOT.reply_message(event.reply_token, message)


@HANDLER.add(MessageEvent, message=ImageMessage)
def handle_content_message(event):
    """
    Reply Image message with results of image description and objection detection
    """
    print(event.message)
    print(event.source.user_id)
    print(event.message.id)
    filename = "{}.jpg".format(event.message.id)
    message_content = LINE_BOT.get_message_content(event.message.id)
    with open(filename, "wb") as f_w:
        for chunk in message_content.iter_content():
            f_w.write(chunk)
    f_w.close()
    image = IMGUR_CLIENT.image_upload(filename, "first", "first")
    link = image["response"]["data"]["link"]
    name = face_recognition(filename)

    if name != "":
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        output = "{0}, {1}".format(name, now)
    else:
        plate = ocr_recognition(link)
        link_ob = object_detection(link, filename)
        if len(plate) > 0:
            output = "License Plate: {}".format(plate)
        else:
            output = object_describe(link)
        link = link_ob

    with open("detect_result.json", "r") as f_r:
        bubble = json.load(f_r)
    f_r.close()
    bubble["body"]["contents"][0]["contents"][0]["contents"][0]["text"] = output
    bubble["header"]["contents"][0]["contents"][0]["contents"][0]["url"] = link
    LINE_BOT.reply_message(
        event.reply_token, [FlexSendMessage(alt_text="Report", contents=bubble)]
    )


if __name__=="__main__":
    app.run()

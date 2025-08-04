import os
import random
import string
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
import redis
from requests import Session, get
import base64
from time import strftime, gmtime


def random_string(length: int):
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for i in range(length))


app = FastAPI()

origins = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    os.getenv("ORIGIN_URI", "https://mpwapi.toaster.gay")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    username=os.getenv("REDIS_USER", None),
    password=os.getenv("REDIS_PASSWORD", None),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0)),
)

# override here or using env variables
CLIENT_ID = "edd1c43c4cd64d388768eeea6718a15f"
REDIRECT_URI = "http://localhost:8000/spotify_api/callback"


@app.get("/requests")
def requests():
    x = redis_client.incr("request_count")
    return {"requests": x}


SPOTIFY_API_AUTHORIZE_TOKEN = random_string(16)


@app.get("/spotify_api/authorize")
def spotify_api_authorize(token: str):
    if token != SPOTIFY_API_AUTHORIZE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
        )

    return {
        "url": f"https://accounts.spotify.com/authorize?response_type=code&client_id={os.getenv('CLIENT_ID', CLIENT_ID)}&scope=user-read-currently-playing&redirect_uri={os.getenv('REDIRECT_URI', REDIRECT_URI)}"
    }


@app.get("/spotify_api/callback")
def spotify_api_callback(code: str):
    if code == None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no code")

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.getenv("REDIRECT_URI", REDIRECT_URI),
    }
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(
            str.encode(
                os.getenv("CLIENT_ID", CLIENT_ID) + ":" + os.getenv("CLIENT_SECRET")
            )
        ).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    s = Session()
    resp = s.post("https://accounts.spotify.com/api/token", headers=headers, data=body)

    if resp.status_code != 200:
        print(resp.content)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="spotify api returned a non-OK status code",
        )

    resp_json = resp.json()

    redis_client.set("spotify_access_token", resp_json["access_token"])
    redis_client.set("spotify_refresh_token", resp_json["refresh_token"])
    return {"success": True}


def spotify_refresh_token():
    body = {
        "grant_type": "refresh_token",
        "refresh_token": redis_client.get("spotify_refresh_token"),
    }
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(
            str.encode(
                os.getenv("CLIENT_ID", CLIENT_ID) + ":" + os.getenv("CLIENT_SECRET")
            )
        ).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    s = Session()
    resp = s.post("https://accounts.spotify.com/api/token", headers=headers, data=body)

    if resp.status_code != 200:
        print(resp.content)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="spotify api returned a non-OK status code",
        )

    resp_json = resp.json()
    print(resp_json)

    redis_client.set("spotify_access_token", resp_json["access_token"])


@app.get("/spotify_api/now_playing")
def spotify_now_playing(loop: bool = False):
    resp = get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={
            "Authorization": "Bearer "
            + redis_client.get("spotify_access_token").decode()
        },
    )

    if resp.status_code == 401 or resp.status_code == 403:
        if loop:
            print(resp.content)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="spotify api returned a non-OK status code",
            )

        spotify_refresh_token()
        return spotify_now_playing(True)

    if resp.content.decode() == "":
        return {"is_playing": False}

    json = resp.json()

    if resp.status_code == 200:
        item = json["item"]
        return {
            "is_playing": True,
            "title": item["name"],
            "artist": ", ".join([a["name"] for a in item["artists"]]),
            "album": item["album"]["name"],
            "album_cover": item["album"]["images"][0]["url"],
            "song_url": item["external_urls"]["spotify"],
            "duration": strftime("%M:%S", gmtime(item["duration_ms"] / 1000)),
            "current": strftime("%M:%S", gmtime(json["progress_ms"] / 1000)),
            "progress": json["progress_ms"] * 100 / item["duration_ms"],
        }
    return {"is_playing": False}


print(
    f'To authorize with Spotify, please pass the string "{SPOTIFY_API_AUTHORIZE_TOKEN}" as a query parameter called "token" to the /spotify_api/authorize endpoint.'
)

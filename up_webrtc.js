const WIDTH = 256;
const HEIGHT = 256;
const WS_URL = "ws://localhost:8000/incoming-frames";

function findRemoteVideo() {
    const videos = Array.from(document.getElementsByTagName("video"));
    const candidates = videos.filter((video, index) => {
        if (index === 0) {
            return false;
        }

        const stream = video.srcObject;
        if (!(stream instanceof MediaStream) || !stream.active) {
            return false;
        }

        const [track] = stream.getVideoTracks();
        if (!track || track.readyState !== "live") {
            return false;
        }

        return true;
    });

    candidates.sort((left, right) => {
        const leftScore = Number(!left.muted) * 10 + left.videoWidth * left.videoHeight;
        const rightScore = Number(!right.muted) * 10 + right.videoWidth * right.videoHeight;
        return rightScore - leftScore;
    });

    return candidates[0] ?? null;
}

async function waitForRemoteTrack() {
    while (true) {
        const video = findRemoteVideo();
        if (video) {
            const [track] = video.srcObject.getVideoTracks();
            if (track) {
                return track;
            }
        }

        await new Promise((resolve) => setTimeout(resolve, 500));
    }
}

function hasVisiblePixel(rgbaBytes) {
    for (let index = 0; index < rgbaBytes.length; index += 4) {
        if (rgbaBytes[index] !== 0 || rgbaBytes[index + 1] !== 0 || rgbaBytes[index + 2] !== 0) {
            return true;
        }
    }

    return false;
}

function rgbFromRgba(rgbaBytes) {
    const rgbBytes = new Uint8Array(WIDTH * HEIGHT * 3);
    for (let src = 0, dst = 0; src < rgbaBytes.length; src += 4, dst += 3) {
        rgbBytes[dst] = rgbaBytes[src];
        rgbBytes[dst + 1] = rgbaBytes[src + 1];
        rgbBytes[dst + 2] = rgbaBytes[src + 2];
    }

    return rgbBytes;
}

async function openSocket() {
    return await new Promise((resolve, reject) => {
        const socket = new WebSocket(WS_URL);
        socket.binaryType = "arraybuffer";
        socket.onopen = () => resolve(socket);
        socket.onerror = () => reject(new Error("WebSocket connection failed"));
    });
}

const track = await waitForRemoteTrack();
const socket = await openSocket();

const processor = new MediaStreamTrackProcessor({track});
const reader = processor.readable.getReader();
const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
const ctx = canvas.getContext("2d", {willReadFrequently: true});

while (true) {
    const {value: frame, done} = await reader.read();
    if (done) {
        break;
    }

    try {
        ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT);
        const rgbaBytes = ctx.getImageData(0, 0, WIDTH, HEIGHT).data;
        if (!hasVisiblePixel(rgbaBytes)) {
            continue;
        }

        if (socket.readyState === WebSocket.OPEN) {
            socket.send(rgbFromRgba(rgbaBytes));
        }
    } finally {
        frame.close();
    }
}

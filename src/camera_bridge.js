const WIDTH = INPUT_W * SCALE_FACTOR;
const HEIGHT = INPUT_H * SCALE_FACTOR;

const rgbaBytes = new Uint8ClampedArray(INPUT_W * INPUT_H * 4);
const imageData = new ImageData(rgbaBytes, INPUT_W, INPUT_H);
const sourceCanvas = document.createElement("canvas");
sourceCanvas.width = INPUT_W;
sourceCanvas.height = INPUT_H;
const FRAME_INTERVAL_MS = 1000 / FPS;
const MAX_OUTGOING_FRAMES = 10;

const canvas = document.createElement("canvas");
canvas.width = WIDTH;
canvas.height = HEIGHT;

const ctx = canvas.getContext("2d", {alpha: false});
const sourceCtx = sourceCanvas.getContext("2d", {alpha: false});
ctx.imageSmoothingEnabled = false;
sourceCtx.imageSmoothingEnabled = false;
const stream = canvas.captureStream(0);
const [videoTrack] = stream.getVideoTracks();
const frameQueue = [];
const outgoingFrameQueue = [];
let socket = null;

let color = 'rgb(255 0 0)';
let color_countdown = 0;
let emptyFrameAngle = 0.0;

function drawEmptyFrame() {
    if (color_countdown === 0) {
        const red = Math.round(((Math.cos(emptyFrameAngle) + 1) / 2) * 255);
        const blue = Math.round(((Math.sin(emptyFrameAngle) + 1) / 2) * 255);
        color = `rgb(${red} 0 ${blue})`;
        emptyFrameAngle += 0.1;
        color_countdown = 30;
    } else {
        color_countdown -= 1;
    }
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.fillStyle = '#ffffff';
    ctx.fillText("SONIC TUNA RULES!", 10, 10);
    videoTrack.requestFrame?.();
}

function drawFrame(rgbBytes) {
    if (!(rgbBytes instanceof Uint8Array) || rgbBytes.length !== INPUT_W * INPUT_H * 3) {
        return;
    }

    for (let src = 0, dst = 0; src < rgbBytes.length; src += 3, dst += 4) {
        rgbaBytes[dst] = rgbBytes[src];
        rgbaBytes[dst + 1] = rgbBytes[src + 1];
        rgbaBytes[dst + 2] = rgbBytes[src + 2];
        rgbaBytes[dst + 3] = 255;
    }

    sourceCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(sourceCanvas, 0, 0, WIDTH, HEIGHT);
    videoTrack.requestFrame?.();
}

function tick() {
    const nextFrame = frameQueue.shift();
    if (nextFrame) {
        drawFrame(nextFrame);
        return;
    }

    drawEmptyFrame();
}

function flushOutgoingFrames() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
    }

    while (outgoingFrameQueue.length > 0 && socket.readyState === WebSocket.OPEN) {
        socket.send(outgoingFrameQueue.shift());
    }
}

function sendFrame(frameBytes) {
    if (!(frameBytes instanceof Uint8Array) || frameBytes.length !== INPUT_W * INPUT_H * 3) {
        return false;
    }

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(frameBytes);
        return true;
    }

    outgoingFrameQueue.push(new Uint8Array(frameBytes));
    while (outgoingFrameQueue.length > MAX_OUTGOING_FRAMES) {
        outgoingFrameQueue.shift();
    }

    return false;
}

function connectFrames() {
    const currentSocket = new WebSocket(WS_URL);
    currentSocket.binaryType = "arraybuffer";
    socket = currentSocket;

    currentSocket.onopen = () => {
        flushOutgoingFrames();
    };

    currentSocket.onmessage = (event) => {
        frameQueue.push(new Uint8Array(event.data));
    };

    currentSocket.onclose = () => {
        if (socket === currentSocket) {
            socket = null;
        }
        setTimeout(connectFrames, 1000);
    };

    currentSocket.onerror = () => {
        currentSocket.close();
    };
}

globalThis.__sonicTunaFrameBridge = {
    sendFrame
};

drawEmptyFrame();
setInterval(tick, FRAME_INTERVAL_MS);
connectFrames();

const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

navigator.mediaDevices.getUserMedia = async (constraints) => {
    const needsVideo = Boolean(constraints && constraints.video);
    if (!needsVideo) {
        return originalGetUserMedia(constraints);
    }

    const originalStream = await originalGetUserMedia(constraints);
    const outgoing = new MediaStream();

    originalStream.getAudioTracks().forEach((track) => outgoing.addTrack(track));
    outgoing.addTrack(videoTrack.clone());

    originalStream.getVideoTracks().forEach((track) => track.stop());

    return outgoing;
};

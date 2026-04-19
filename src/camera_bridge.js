const WIDTH = INPUT_W * SCALE_FACTOR;
const HEIGHT = INPUT_H * SCALE_FACTOR;

const sourceCanvas = new OffscreenCanvas(INPUT_W, INPUT_H);
const FRAME_INTERVAL_MS = 1000 / FPS;
const MAX_OUTGOING_FRAMES = 10;

const canvas = new OffscreenCanvas(WIDTH, HEIGHT);

const ctx = canvas.getContext("2d", {alpha: false});
const sourceCtx = sourceCanvas.getContext("2d", {alpha: false});
ctx.imageSmoothingEnabled = false;
sourceCtx.imageSmoothingEnabled = false;
const stream = canvas.captureStream(0);
const [videoTrack] = stream.getVideoTracks();
const frameQueue = [];
const frameQueueLimit = 10;
const outgoingFrameQueue = [];
let socket = null;

let emptyFrameAngle = 0.0;

const imageData = sourceCtx.createImageData(INPUT_W, INPUT_H);
const uint32View = new Uint32Array(imageData.data.buffer);

function drawEmptyFrame() {
    const red = Math.round(((Math.cos(emptyFrameAngle) + 1) / 2) * 255);
    const blue = Math.round(((Math.sin(emptyFrameAngle) + 1) / 2) * 255);
    let color = `rgb(${red} 0 ${blue})`;
    emptyFrameAngle += 0.01;

    ctx.fillStyle = color;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.fillStyle = '#ffffff';
    ctx.fillText("SONIC TUNA RULES!", 10, 10);
    videoTrack.requestFrame?.();
}

function drawFrame(grayBytes) {
    if (!(grayBytes instanceof Uint8Array) || grayBytes.length !== INPUT_W * INPUT_H) {
        return;
    }

    for (let i = 0; i < grayBytes.length; i += 1) {
        const val = grayBytes[i];
        uint32View[i] = (255 << 24) | (val << 16) | (val << 8) | val;
    }

    sourceCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(sourceCanvas, 0, 0, WIDTH, HEIGHT);
    ctx.fillStyle = '#000'; // двойная отправка кадра, чтобы сформировать на принимающей стороне более устойчивое изображение
    sourceCtx.fillRect(0,0,8,8)
    videoTrack.requestFrame?.();
    ctx.fillStyle = '#fff';
    sourceCtx.fillRect(0,0,8,8)
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
    if (!(frameBytes instanceof Uint8Array) || frameBytes.length !== INPUT_W * INPUT_H) {
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
        while (frameQueue.length > frameQueueLimit) frameQueue.shift();
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

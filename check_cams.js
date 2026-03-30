const videos = document.getElementsByTagName("video");
if(videos.length < 2) return false;
for(var i = 1; i < videos.length; ++i) {
    const src = videos[i].srcObject
    if(src != null && src instanceof MediaStream && src.active){
        return true;
    }
}
return false;
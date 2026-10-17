"""OpenCV frame review. Browsing history never re-runs inference or alarms."""
from __future__ import annotations


class FrameReview:
    def __init__(self, paused=False, fps=30., history_limit=300):
        import cv2
        self.cv = cv2
        self.title = 'YOLO11 Fall Detection - Frame Review'
        self.paused, self.fps = paused, fps
        self.history, self.index, self.limit = [], 0, history_limit
        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        cv2.resizeWindow(self.title, 1100, 760)

    def show(self, image):
        ok, encoded = self.cv.imencode('.jpg', image, [self.cv.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError('Cannot cache display frame')
        self.history.append(encoded)
        if len(self.history) > self.limit:
            self.history.pop(0)
        self.index = len(self.history) - 1
        return self.wait()

    def wait(self, ended=False):
        cv = self.cv
        if not self.history:
            return True
        if ended:
            self.paused = True
        while True:
            image = cv.imdecode(self.history[self.index], cv.IMREAD_COLOR)
            image = cv.copyMakeBorder(image, 0, 62, 0, 0, cv.BORDER_CONSTANT, value=(20, 20, 20))
            status = 'PAUSED' if self.paused else 'PLAYING'
            if ended:
                status += ' | END OF VIDEO - replay cached frames with R/Space'
            cv.putText(image, status, (10, image.shape[0] - 40), cv.FONT_HERSHEY_SIMPLEX, .45, (0, 220, 255), 1)
            cv.putText(image, 'Space: play/pause | N: next | P: previous | R: first cached | Q: quit',
                       (10, image.shape[0] - 15), cv.FONT_HERSHEY_SIMPLEX, .44, (255, 255, 255), 1)
            cv.imshow(self.title, image)
            key = cv.waitKeyEx(30 if self.paused else max(1, round(1000 / self.fps)))
            if cv.getWindowProperty(self.title, cv.WND_PROP_VISIBLE) < 1 or key in (ord('q'), ord('Q'), 27):
                return False
            if key in (ord('p'), ord('P')):
                self.index, self.paused = max(0, self.index - 1), True
            elif key in (ord('r'), ord('R')):
                self.index, self.paused = 0, True
            elif key in (ord('n'), ord('N')):
                self.paused = True
                if self.index < len(self.history) - 1:
                    self.index += 1
                elif not ended:
                    return True
            elif key == ord(' '):
                if ended and self.index == len(self.history) - 1:
                    self.index = 0
                self.paused = not self.paused
            elif not self.paused:
                if self.index < len(self.history) - 1:
                    self.index += 1
                elif ended:
                    self.paused = True
                else:
                    return True

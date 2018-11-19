import picamera
from queue import Empty
from time import time, sleep
import numpy as np
import cv2
import imutils
import math
import os
import serial
import imageio
import logging
import signal
from qlogging import PUBLogger, SUBLogger
from threading import Thread

USE_MULTIPROCESSING = True
USE_THREADING = not USE_MULTIPROCESSING

DEBUG_FRAMES = True

if USE_MULTIPROCESSING is True and \
    USE_THREADING is False:
    from multiprocessing.managers import BaseManager, BaseProxy, NamespaceProxy
    from multiprocessing import Manager
elif USE_MULTIPROCESSING is False and \
    USE_THREADING is True:
    from queue import Queue

class MyOutput():
    '''
    Class used by PiCamera object to record the frames coming from the camera. 
    
    The PiCamera class assumes there is a write method (and optionally a flush method).
    Since we've got to pass the data to the image processor by the means of a queue.
    
    Check https://picamera.readthedocs.io/en/release-1.13/recipes2.html#custom-outputs
    
    '''
    def __init__(self, queue, resolution, state):
        '''
        Initialize MyOutput object.
        
        Init params:
        queue -- The queue onto which raw frames are pushed to.
        resolution -- The resolution of the pushed frames. 2-element tuple signifying (width, height).
        state -- A dictionary containing important markers about the current frame: time, red/blue/green GB; TL;DR metadata. 
                Frames won't get pushed into the queue if state is set to None.
        '''
        self._queue = queue
        self._resolution = resolution[::-1] + (3,)
        self.state = state

    def write(self, s):
        '''
        Write method required by PiCamera class.
        
        Keyword params:
        s -- A bytes object containing the actual image that got captured.
        '''
        
        self._buffer = np.frombuffer(s, np.uint8).reshape(*self._resolution)
        self.flush()
    
    def flush(self):
        '''
        Flush method that needs to be called to push the data to the queue. 
        Apart from being called by write method every time, it also gets called at the end of the recording by the PiCamera object.
        '''
        if self._buffer is not None and \
            self.state is not None:
            self._queue.put([
                self._buffer,
                self._state
            ], block = False)
            self._buffer = None
        
    @property
    def state(self):
        '''
        Returns the dictionary containing important markers about the current frame(s).
        '''
        return self._state
    
    @state.setter
    def state(self, state):
        '''
        Set important markers about the current frame(s).
        
        Contains information about the current environment that the camera "sees".
        The state variable is necessary for validating the data later on.
        '''
        self._state = state

class CameraSource(Thread):
    '''
    Class used to gather BGR frames from the PiCamera on a separate thread while pushing them
    in a queue that gets consumed on the fly by a process which analyses the data.
    
    Only one instance of this class can exist at a moment.
    
    The format of each pushed object into the queue is tailored to the GiggleBotQAValidation class. That is, each object (list)
    contains a (captured) frame and metadata about it stored as a dictionary. This class can be adapted to accept any format for
    these pushed objects.

    On how to get a square sync signal from the (not helpful though): https://www.raspberrypi.org/forums/viewtopic.php?t=190314
    '''
    
    def __init__(self, 
        queue, 
        camera_settings,
        log_queue,
        state = {}, 
        output_resize = (480, 272), 
        frame_callback = None,
        dry_run = False,
        log_level = logging.DEBUG):
        '''
        Initialise the CameraSource object.
        
        Init params:
        queue -- The queue into which frames are pushed in one by one. The queue must be infinite in size.
        camera_settings -- Settings of the PiCamera that can be accessed as an attribute after having initialized the object.
        state -- A dictionary containing important markers necessary for validating the data. Not mandatory if you've passed an object for frame_callback param.
        output_resize -- The actual resolution of the frames that get pushed into the queue. Must be >= than what's specified in camera_settings param. 2-element tuple of (width, height).
        frame_callback -- Optional object to use for preparing the next frame for whatever it's needed. Must have an update method which returns a state dictionary.
        dry_run -- Set to True to test the PiCamera. Cannot start the thread with this setting on as the camera is closed as soon as this constructor reaches its end.
        log_queue -- A queue to push logs to. Uses the implementation found in advanced_logging.
        log_level -- A selection from one of the 5 levels: DEBUG, INFO, WARN, ERROR, CRITICAL.

        The constructor waits 2 seconds for the camera to initialize after the PiCamera object is created. This is a suggestion found in PiCamera's documentation.
        
        The output_resize param has to be a 2-element tuple containing the width and height of the recorded frames.
        '''
        super(CameraSource, self).__init__(group = None, target = None, name = 'CameraSource')
        
        self._queue = queue
        self._camera_settings = camera_settings
        self._output_resize = output_resize
        self._frame_callback = frame_callback
        if len(state) == 0:
            state = None
        self._output = MyOutput(queue, output_resize, state = state)

        self._logger = PUBLogger(log_queue, log_level)
        self.pause = False

        self._stop_thread = False
        self._terminated = False
        
        self._failed = False
        self._logger.info('initializing picamera..')
        try:
            self.camera = picamera.PiCamera()
            self.camera.start_preview()
            for setting in list(camera_settings.keys()):
                setattr(self.camera, setting, camera_settings[setting])
            sleep(2.0)
            self._logger.debug('CameraSource object created')
            if dry_run is True:
                self.camera.close()
        except picamera.PiCameraError as error:
            self._logger.critical(str(error), exc_info = 1)
            self._failed = True
        
    def run(self):
        '''
        This method must not be called from the user space. This gets called by the start method,
        which in turn, is used to trigger this method.
        
        This method continuously pushes frames into the queue. To stop it, call the stop method.
        '''
        try:
            frame_period = 1.0 / self.camera.framerate
            while self._stop_thread is False:

                # change the color of the LEDs
                if self._frame_callback is not None and self.pause is False:
                    try:
                        self._output.state = self._frame_callback.update()
                    except Exception as error:
                        self._logger.error(str(error), exc_info = 1)
                        self._frame_callback.initialize()
                        if self._frame_callback.failed() is True:
                            self._logger.critical('failed reinitializing the neopixels')
                            raise Exception('failed reinitializing the neopixels')
                        else:
                            self._logger.info('successfully reinitialized the neopixels from certain death')
                            continue
                
                # make sure the next capture is indeed what we want
                sleep(frame_period)

                # capture the frame
                try:
                    if self.pause is False:
                        if self._output.state is None:
                            self._logger.debug('not capturing frame from camera due to state being set to None')
                        elif 'id' in self._output.state:
                            self._logger.debug('capturing frame from picamera with id=' + str(self._output.state['id']))
                            self.camera.capture_sequence([self._output],
                                format = 'bgr',
                                resize = self._output_resize,
                                use_video_port = True)
                            self._logger.debug('captured frame with id=' + str(self._output.state['id']))

                except (picamera.exc.PiCameraError, Exception) as error:
                    self._logger.critical(str(error), exc_info = 1)
                    break

        except Exception as error:
            self._logger.critical(str(error), exc_info = 1)
            self._failed = True
        finally:
            self.camera.close()
            self._terminated = True
            self._logger.info('stopped collecting picamera frames')
            
    def stop(self, blocking = True):
        '''
        Stops the run method that got called by start method.
        
        Keyword params:
        blocking -- Boolean to specify if it awaits for the run method to stop.
        '''
        self._logger.debug('got a stop command')
        self._stop_thread = True
        if blocking is True:
            while self._terminated is False:
                sleep(0.001)
        self._logger.debug('terminated')

    @property
    def failed(self):
        '''
        Returns True if instantiating the PiCamera has failed (when calling the constructor) or
        if something critically bad happens during the operation of the camera.
        '''
        return self._failed
                
    @property
    def state(self):
        return self._output.state
    
    @state.setter
    def state(self, state):
        self._output.state = state
        
    @property
    def pause(self):
        '''
        Checks if recording is paused or not.
        '''
        return self._pause
    
    @pause.setter
    def pause(self, val):
        '''
        Pauses the recording process or resumes it.
        
        Keyword params:
        val -- True to pause it and false to resume it.
        '''
        self._logger.info('set pause to ' + str(val))
        self._pause = val
    

class GiggleBotQAValidation(Thread):
    '''
    Instantiate one or multiple objects of this class to process the images coming from the camera (by passing down
    the queue object to every one of these objects).
    
    The result of this process is a simple binary answer (yes/no) if the GiggleBot board has passed the LED test.
    
    Keep in mind that the metadata dictionary that this object receives through the process_queue param
    must have a single key called 'leds' and a string associated to it representing the color
    of the LEDs that should be detected. The color string must be present in the config['color-boundaries'] list.
    '''
    
    def __init__(self, 
        process_queue, 
        config,
        log_queue,
        stop_when_empty = False, 
        stop_when_failed = False,
        save_images_for_debugging = False,
        path_for_images = './',
        log_level = logging.DEBUG):
        '''
        Init params:
        process_queue -- Queue from which to extract PiCamera frames and metadata.
        config -- A dictionary containing settings on how to process the incoming frames.
        stop_when_empty -- Flag to stop the thread when there are no more elements in the queue.
        stop_when_failed -- Flag to stop the thread when the QA test has failed.
        save_images_for_debugging -- To save different frames/masks/processed stuff to the disk for debugging purposes.
        path_for_images -- Location where to save the frames/masks/processed stuff for debugging purposes.
        log_queue -- A queue to push logs to. Uses the implementation found in advanced_logging.
        log_level -- A selection from one of the 5 levels: DEBUG, INFO, WARN, ERROR, CRITICAL.

        An example config dictionary should look like this:
        {
            'color-boundaries': [
                ('red', [0, 165, 128], [15, 255, 255]),
                ('red', [165, 165, 128], [179, 255, 255]),
                ('green', [35, 165, 128], [75, 255, 255]),
                ('blue', [90, 165, 128], [133, 255, 255])
            ], # HSV colors
            'leds': 7,
            'acceptable-leading-color-ratio': 0.95,  # 95% of the detected color is the actual one present
            'acceptable-ratio-between-most-popular-colors': 0.05, # 2nd most predominant / most predominant color
            'gaussian-blur': (5,5),
            'binary-threshold': 200,
            'minimum-circle-lines': 9,
            'maximum-circle-lines': 22,
            'minimum-circle-size': 101,
            'scale-2nd-circle': 1.7
        }
        '''
        super(GiggleBotQAValidation, self).__init__(group = None, target = None, name = 'GiggleBotQAValidation')
        
        self._procq = process_queue
        self._stop_when_empty = stop_when_empty
        self._stop_when_failed = stop_when_failed
        self._save_images = save_images_for_debugging
        self._path_images = path_for_images
        self._stop_thread = False
        self._terminated = False
        self._logger = PUBLogger(log_queue, log_level)

        self._boundaries = config['color-boundaries']
        self._no_leds = config['leds']
        self._acceptable_leading_color_ratio = config['acceptable-leading-color-ratio']
        self._acceptable_ratio_between_most_popular_colors = config['acceptable-ratio-between-most-popular-colors']
        self._gaussian_blur = tuple(config['gaussian-blur'])
        self._binary_threshold = config['binary-threshold']
        self._minimum_circle_lines = config['minimum-circle-lines']
        self._maximum_circle_lines = config['maximum-circle-lines']
        self._minimum_circle_size = config['minimum-circle-size']
        self._scale_2nd_circle = config['scale-2nd-circle']
        
        self.failed_qa = False # check this to see when the test fails
        self.stats = {
            'start_time': None,
            'last_update': None
        } # use this to calculate how much time this process worked for
        # the 'last_update' value updates only when failed_qa is set to False

        self._logger.info('GiggleBotQAValidation object created')
        
    def run(self):
        '''
        This method must not be called from the user space. This gets called by the start method,
        which in turn, is used to trigger this method.

        This method continuously processes frames coming down the queue pipe. To stop it, call the stop method.
        '''
        while self._stop_thread is False:
            try:
                if self._stop_when_failed is True and self.failed_qa is True:
                    self._logger.warn('failed qa test and further incoming frames won\'t get processed')
                    break
                frame, metadata = self._procq.get_nowait()
                if self.failed_qa is False:
                    self._logger.debug('received valid frame')
                    try:
                        self._do_qa_on_frame(frame, metadata)
                    except Exception as msg:
                        self._logger.error(str(msg), exc_info = 1)
            except Empty:
                if self._stop_when_empty is True and self._procq.qsize() == 0:
                    self._logger.warn('queue empty; ending the operation')
                    break
            finally:
                sleep(0.010)
        self._terminated = True
    
    def stop(self, blocking = True):
        '''
        Stops the run method that got called by start method.

        Keyword params:
        blocking -- Boolean to specify if it awaits for the run method to stop.
        '''
        self._logger.debug('got a stop command')
        self._stop_thread = True
        if blocking is True:
            while self._terminated is False:
                sleep(0.001)
        self._logger.debug('terminated')
                
    def _do_qa_on_frame(self, frame, metadata):
        '''
        The method which gets repeatedly called by the run method.
        
        Keyword params:
        frame -- The BGR frame that needs to be analyzed.
        metadata -- Data about the current frame that's used for validation.
        
        The method determines if the test has failed so far or if it still keeps on going. This method
        also calls the _do_frame_analysis method.
        '''
        if self.stats['start_time'] is None:
            self.stats['start_time'] = time()
        self.stats['last_update'] = time()
        color_dist, leds = self._do_frame_analysis(frame, id = metadata['id'])
        
        self._logger.debug('color distribution = ' + str(color_dist) + ', no LEDs = ' + str(leds) + ' w/ target = ' + metadata['leds'])
        
        if self._no_leds != leds:
            self.failed_qa = True
            self._logger.warn('failed qa test ' + str(self._no_leds) + '!=' + str(leds))
            return
        
        pixels = list(color_dist.values())
        primary_no_pixels = color_dist[metadata['leds']] + 1
        total_pixels = sum(pixels)
        
        if total_pixels <= 100:
            self.failed_qa = True
            self._logger.warn('failed because under 100 pixels have been detected')
            return
        
        if primary_no_pixels / total_pixels <= self._acceptable_leading_color_ratio:
            self.failed_qa = True
            self._logger.warn('failed qa on primary color test')
            return
        
        # we don't have to worry about duplicates, because if it were the case,
        # this would anyway not get executed since it would stop at the above instructions
        pixels.remove(max(pixels))
        highest_2nd = max(pixels)
        if highest_2nd / primary_no_pixels > self._acceptable_ratio_between_most_popular_colors:
            self.failed_qa = True
            self._logger.warn('failed qa on secondary/primary color test')
            return

        self._logger.debug('passed qa on frame (id=' + str(metadata['id']) + ') with ' + str(leds) + ' LEDs detected, ' \
                        + str(100 * primary_no_pixels / total_pixels) + '% leading color majority, '\
                        + str(100 * highest_2nd / primary_no_pixels) + '% ratio between 2nd/1st')
    
    def _do_frame_analysis(self, frame, id):
        '''
        Returns the color percentage for each one listed in _boundaries dictionary and 
        the number of detected LEDs on the GiggleBot.
        
        The 1st element returned is a dictionary, where each key is the color we want to detect and each
        value represents the number of pixels that fit into that category.
        The 2nd element returned is the number of detected LEDs.
        
        Keyword params:
        frame -- The frame to be analyzed.
        id -- The id of the frame in case we want to have a way to do identification when debugging.
        '''

        (height, width) = frame.shape[0:2]
        self._logger.debug('received ' + str(width) + 'x' + str(height) + ' frame')

        if self._save_images is True:
            self._logger.debug('saving frame to ' + 'images/' + str(id) + '.jpeg')
            os.makedirs(self._path_images + 'images/', exist_ok=True)
            cv2.imwrite(self._path_images + 'images/' + str(id) + '.jpeg', frame) 
        
        # convert to grayscale, do a binary threshold and find the contours
        self._logger.debug('convert frame to grayscale')
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._save_images is True:
            self._logger.debug('saving grayscale frame to ' + 'grayscales/' + str(id) + '.jpeg')
            os.makedirs(self._path_images + 'grayscales/', exist_ok=True)
            cv2.imwrite(self._path_images + 'grayscales/' + str(id) + '.jpeg', gray)

        self._logger.debug('apply gaussian blur with ' + str(self._gaussian_blur) + ' filter')
        blurred = cv2.GaussianBlur(gray, self._gaussian_blur, 0)
        if self._save_images is True:
            self._logger.debug('saving gaussian-blurred frame to ' + 'gaussian_blur/' + str(id) + '.jpeg')
            os.makedirs(self._path_images + 'gaussian_blur/', exist_ok=True)
            cv2.imwrite(self._path_images + 'gaussian_blur/' + str(id) + '.jpeg', blurred)

        self._logger.debug('apply binary threshold of ' + str(self._binary_threshold) + ' to frame')
        thresh = cv2.threshold(blurred, self._binary_threshold, 255, cv2.THRESH_BINARY)
        if self._save_images is True:
            self._logger.debug('saving thresholded frame to '+ 'thresholded/' + str(id) + '.jpeg')
            os.makedirs(self._path_images + 'thresholded/', exist_ok=True)
            cv2.imwrite(self._path_images + 'thresholded/' + str(id) + '.jpeg', thresh[1])

        contours = cv2.findContours(thresh[1].copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = contours[0] if imutils.is_cv2() else contours[1]

        
        # filter out the "bad" contours
        # keep the enclosed shapes that resemble a circle
        # and that have a size appropriate to that of a GB LED
        contours_list = []
        radiuses = []
        centers = []
        areas = []
        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.01 * perimeter, True, True)
            area = cv2.contourArea(contour)
            
            if (len(approx) >= self._minimum_circle_lines) and (len(approx) <= self._maximum_circle_lines) and area >= self._minimum_circle_size:
                contours_list.append(contour)
                radiuses.append(perimeter / math.pi / 2)
                M = cv2.moments(contour)
                cX = int(M['m10'] / M['m00'])
                cY = int(M['m01'] / M['m00'])
                centers.append((cX, cY))
                areas.append(area)

        if len(radiuses) == 0:
            self._logger.warn('no circles/LEDs found')
            return None, 0
        else:
            self._logger.debug('found ' + str(len(radiuses)) + ' LEDs')

        # determine the mean radius of the detected circles
        # and calculate a bigger one related to the original
        scale = self._scale_2nd_circle
        intern_radius = np.mean(radiuses)
        extern_radius = intern_radius * scale
        intern_radius = math.ceil(intern_radius)
        extern_radius = math.ceil(extern_radius)
        extern_diam = extern_radius * 2
        self._logger.debug('following circle sizes found = ' + str(areas))
        self._logger.debug('avg circle radius = ' + str(intern_radius))
        
        # draw the bigger circles and remove the inner ones by setting them to black
        mask = np.zeros((height, width, 3), np.uint8)
        for center in centers:
            cv2.circle(mask, center, extern_radius, (255, 255, 255), -1)
            cv2.circle(mask, center, intern_radius, (0, 0, 0), -1)

        if self._save_images is True:
            os.makedirs(self._path_images + 'masks/', exist_ok=True)
            cv2.imwrite(self._path_images + 'masks/' + str(id) + '.jpeg', mask) 
        
        # use the previous mask to get the relevant pixels from the orignal frame
        out = np.zeros((height, width, 3), np.uint8)
        cv2.bitwise_and(frame, mask, out)
        # writearray(out[:,:,[2,1,0]], "processed/img_{}.png".format(self.counter), 'RGB')
        # self.counter += 1
        if self._save_images is True:
            self._logger.debug('saving masked frame to '+ 'masked/' + str(id) + '.jpeg')
            os.makedirs(self._path_images + 'masked/', exist_ok=True)
            cv2.imwrite(self._path_images + 'masked/' + str(id) + '.jpeg', out) 
        
        # convert to HSV to be able to categorize the colors based on _boundaries dictionary
        cv2.cvtColor(out, cv2.COLOR_BGR2HSV, out)         
        transpose_list = list(zip(*self._boundaries))
        colors = {}
        for elem in transpose_list[0]:
            colors[elem] = 0
        for (color, lower, upper) in self._boundaries:
            lower = np.array(lower, dtype = np.uint8)
            upper = np.array(upper, dtype = np.uint8)
            
            mask = cv2.inRange(out, lower, upper)
            filtered_channel = cv2.bitwise_and(out, out, mask = mask)
            gray_channel = cv2.cvtColor(filtered_channel, cv2.COLOR_BGR2GRAY)
            detected_pixels = cv2.countNonZero(gray_channel)
            colors[color] += detected_pixels
        
        return colors, len(contours_list)

    def join(self):
        '''
        Join the main thread when the queue gets empty or if the QA test fails.

        Only appliable if either stop_when_empty or stop_when_failed parameters for the constructor
        are set to True.
        '''
        self._logger.debug('called join method')
        if self._stop_when_empty is True or self._stop_when_failed is True:
            while self._procq.qsize() > 0 and self.failed_qa is False and self._terminated is False:
                sleep(0.001)
        self._logger.info('terminated')

    @property
    def queue_empty(self):
        '''
        Returns True if the queue is empty, False otherwise.
        '''
        return self._procq.qsize() == 0

class LEDChanger():
    '''
    Changes the color of the GB's LEDs by calling update method. A new id is returned
    with each call to update method. If a subsequent call is made too fast, the LEDs 
    might not switch colors, but the ID will still get changed.

    Requires a microbit.
    '''

    def __init__(self, port, cooldown, color_config):
        '''
        Init params:
        port -- The serial port to which the microbit is connected to.
        cooldown -- How much time needs to pass before being able to switch colors again.
        color_config -- A dictionary containing pairs of color commands (the actual cmd that's sent 
                        to the MB) and color names (strings that are to be recognized by GiggleBotQAValidation class).
        
        color_config must have this form:
        {
            'color-codes': [b'r', b'g', b'b'],
            'color-names': ['red', 'green', 'blue']
        }
        '''
        self._port = port
        self._counter = 0
        self._cooldown = cooldown
        self._last_update = 0
        self._colors = color_config['color-codes']
        self._color_names = color_config['color-names']

    def update(self):
        now_time = time()
        if now_time - self._last_update >= self._cooldown:
            with serial.Serial(self._port, 115200) as ser:
                ser.write(self._colors[self._counter % 3])
                ser.flush()

                self._state = {
                    'leds': self._color_names[self._counter % 3],
                    'id': self._counter
                }
            self._counter += 1
            self._last_update = time()

        return self._state

if USE_MULTIPROCESSING is True:
    class LEDChangerProxy(NamespaceProxy):
        _exposed_ = ('__getattribute__', '__setattr__', '__delattr__', 'update')

        def update(self):
            callmethod = object.__getattribute__(self, '_callmethod')
            return callmethod('update')

def prepare_manager():
    '''
    Configures a multiprocessing manager by registering the classes of this module,
    along with adding the required setters and getters for it in order to easily access 
    the attributes of a given class attributes.

    Also configures a manager for shared objects between all proxy-managed instances. Used for queues.

    The 1st returned argument is the manager for the classes' objects and the 2nd one is the manager
    for shared objects.

    To access an attribute of a given class, do this:
    obj.set_attr('name_of_attribute', value_to_set)
    obj.get_attr('name_of_attribute')

    That's opposed to accessing them like obj.name_of_attribute.
    '''
    class MyManager(BaseManager):
        pass

    def create_setter_getters(cls):
        '''
        Creates set_attr and get_attr public methods for cls class.

        The problem with the multiprocessing managers is that the proxy can only reach methods, but not attributes.
        To fix this, we can create 2 proxy methods to interact with the said attributes.
        '''

        def set_attr(self, attr, val):
            setattr(self, attr, val)

        def get_attr(self, attr):
            return getattr(self, attr)

        setattr(cls, set_attr.__name__, set_attr)
        setattr(cls, get_attr.__name__, get_attr)

        return cls

    MyManager.register(CameraSource.__name__, create_setter_getters(CameraSource))
    MyManager.register(GiggleBotQAValidation.__name__, create_setter_getters(GiggleBotQAValidation))
    MyManager.register(LEDChanger.__name__, LEDChanger, LEDChangerProxy)    

    sync_master = Manager()

    return (MyManager, sync_master)    

def _do_multiprocessing(settings):
    (MyManager, sync_manager) = prepare_manager()

    camera_settings = settings['camera']
    qa_settings = settings['qa']
    led_switcher_settings = settings['led-switcher']

    with MyManager() as manager:
        log_queue = sync_manager.Queue(0)
        logger_sub = SUBLogger(log_queue, stdout=True, fileout=False, level=logging.DEBUG)
        logger_sub.start()
        logger = PUBLogger(log_queue)

        queue = sync_manager.Queue(0)
        led_changer = manager.LEDChanger('/dev/ttyACM0', 0.025, led_switcher_settings)
        producer = manager.CameraSource(queue, camera_settings, log_queue, frame_callback = led_changer)
        
        consumers = []
        for i in range(2):
            c = manager.GiggleBotQAValidation(queue, 
                qa_settings,
                log_queue,
                stop_when_empty = False,
                stop_when_failed = False,
                save_images_for_debugging = DEBUG_FRAMES,
                path_for_images = './test/')
            c.start()
            consumers.append(c)
        producer.start()

        start = time()
        end = start + 10
        while time() <= end:
            if True in list(map(lambda x: x.get_attr('failed_qa'), consumers)):
                break
            sleep(0.001)
        producer.stop()

        while True:
            if True in list(map(lambda x: x.get_attr('failed_qa'), consumers)):
                break
            if len(list(map(lambda x: x.get_attr('failed_qa'), consumers))) == len(consumers):
                break
            sleep(0.001)

        for c in consumers:
            c.stop()
            c.join()
        logger_sub.stop()

def _do_threading(settings):
    image_queue = Queue()
    log_queue = Queue()
    logger_sub = SUBLogger(log_queue, stdout=True, fileout=False, level=logging.DEBUG)
    logger_sub.start()
    logger = PUBLogger(log_queue)

    camera_settings = settings['camera']
    qa_settings = settings['qa']
    led_switcher_settings = settings['led-switcher']

    led_changer = LEDChanger('/dev/ttyACM0', 0.025, led_switcher_settings)
    producer = CameraSource(image_queue, camera_settings, log_queue, frame_callback = led_changer)
    consumer = GiggleBotQAValidation(image_queue, 
                                    qa_settings, 
                                    log_queue,
                                    stop_when_empty = True, 
                                    save_images_for_debugging = True,
                                    path_for_images = './test/')

    producer.start()
    sleep(10)

    consumer.start()
    producer.stop()

    # wait for the thread to finish to be able to create the GIFs
    while consumer.is_alive():
        sleep(0.001)
    
    # all the rest is for generating the GIFs from the collected JPEGs
    if DEBUG_FRAMES is True:
        list_dirs = []
        os.chdir('./test/')
        for entry in os.scandir('./'):
            if entry.is_dir():
                list_dirs.append(entry.path)
        os.makedirs('./gifs', exist_ok = True)

        images = {}
        for directory in list_dirs:
            print('gathering images from ' + directory + ' dir ..')
            for file_name in os.listdir(directory):
                if file_name.endswith('.jpeg'):
                    file_path = os.path.join(directory, file_name)
                    if directory[2:] not in images:
                        images[directory[2:]] = []
                    else:
                        images[directory[2:]].append(imageio.imread(file_path))
        
        for key, values in images.items():
            print('saving ' + key + '.gif ..')
            imageio.mimsave('./gifs/' + key + '.gif', values)
        os.chdir('..')

    logger_sub.stop()

if __name__ == "__main__":

    os.system('taskset -p 0xff %d' % os.getpid())

    settings = {
        'camera' : {
            'iso': 100,
            'shutter_speed': 3000,
            'preview_alpha': 255,
            'resolution': (480, 272),
            'sensor_mode': 1,
            'rotation': 0,
            'framerate': 40,
            'brightness': 50,
            'awb_mode': 'off',
            'awb_gains': 1.5
        },
        'qa' : {
            'color-boundaries': [
                ('red', [0, 165, 128], [15, 255, 255]),
                ('red', [165, 165, 128], [179, 255, 255]),
                ('green', [35, 165, 128], [75, 255, 255]),
                ('blue', [90, 165, 128], [133, 255, 255])
            ], # HSV colors
            'leds': 7,
            'acceptable-leading-color-ratio': 0.95,  # 95% of the detected color is the actual one present
            'acceptable-ratio-between-most-popular-colors': 0.05, # 2nd most predominant / most predominant color
            'gaussian-blur': (5,5),
            'binary-threshold': 200,
            'minimum-circle-lines': 5,
            'maximum-circle-lines': 45,
            'minimum-circle-size': 101,
            'scale-2nd-circle': 1.7
        },
        'led-switcher': {
            'color-codes': [b'r', b'g', b'b'],
            'color-names': ['red', 'green', 'blue']
        }
    }

    if USE_MULTIPROCESSING is True:
        _do_multiprocessing(settings)

    if USE_THREADING is True:
        _do_threading(settings)

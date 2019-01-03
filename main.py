import gbtest
import imageio
import logging
import json
import os, signal
import shutil
import gopigo3

from easygopigo3 import EasyGoPiGo3
from qlogging import SUBLogger, PUBLogger
from threading import Thread, Event
from multiprocessing import Queue
from time import sleep, time
from rpi_ws281x import Adafruit_NeoPixel, Color

RED = (255, 0, 0)
MAGENTA = (255, 0, 255)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
ORANGE = (255, 99, 71)

PRESSED = 1
RELEASED = 0
threads = []

class PlayGoPiGo3LED(Thread):
    '''
    Class to control the GoPiGo3 mascot's eyes.

    Can be used to play different animations on the LEDs as opposed to just turning them solid to a specific color.
    '''
    def __init__(self, led_object):
        '''
        Init params:
        led_object -- EasyGoPiGo3 object.
        '''
        super(PlayGoPiGo3LED, self).__init__()
        self._led = led_object
        self._event = Event()

        self._type = 'solid'
        self._color = (0, 0, 0)
        self._last_solid = False

    def run(self):
        while self._event.is_set() is False:
            getattr(self, '_' + self._type)(self._color)        
            sleep(0.05)

    def stop(self):
        self._event.set()
        while self.is_alive() is True:
            sleep(0.0001)
        self._solid(self._color)

    def play(self, typep, color):
        '''
        Key params:
        typep -- Modes of LEDs - can be 'solid', 'blink' or 'breathe'.
        color -- 3-element tuple representing an RGB color.
        '''
        self._type = typep
        self._color = color

    def _solid(self, color):
        self._led.set_eye_color(color)
        self._led.open_eyes()
        sleep(0.2)

    def _blink(self, color):
        self._led.set_eye_color(color)
        self._led.open_eyes()
        sleep(0.2)
        self._led.set_eye_color((0, 0, 0))
        self._led.open_eyes()
        sleep(0.2)
    
    def _breathe(self, color):
        for step in range(128):
            aux = tuple(map(lambda x: int(x * step / 128.0), color))
            self._led.set_eye_color(aux)
            self._led.open_eyes()
            sleep(0.001)
        for step in range(128):
            aux = tuple(map(lambda x: int(x * (128.0 - step) / 128.0), color))
            self._led.set_eye_color(aux)
            self._led.open_eyes()
            sleep(0.001)

class LEDChangerProxy(gbtest.LEDChangerProxy):
    '''
    Proxy for the multiprocessing manager.
    '''
    _exposed_ = gbtest.LEDChangerProxy._exposed_ + ('initialize', 'failed', 'reset')

    def initialize(self):
            callmethod = object.__getattribute__(self, '_callmethod')
            return callmethod('initialize')

    def failed(self):
            callmethod = object.__getattribute__(self, '_callmethod')
            return callmethod('failed')

    def reset(self):
            callmethod = object.__getattribute__(self, '_callmethod')
            return callmethod('reset')

class LEDChanger:
    '''
    Used to change the color of the LEDs.
    '''
    def __init__(self, config):
        '''
        Init params:
        config -- Dictionary containing a 'color-codes' key that points to a list made of that that many elements as there are colors.
            Each element of this list contains that many elements as many LEDs there are to control. Each of that list represents an RGB color.
            Also, 'color names' key is present (strings that are to be recognized by GiggleBotQAValidation class)
            and the GPIO port to control the NeoPixel LEDs.

        config must have this form:
        {
            'color-codes': [[(255, 0, 0)] * 9, [(0, 255, 0)] * 9, [(0, 0, 255)] * 9],
            'color-names': ['red', 'green', 'blue'],
            'gpio-port': 12
        }
        '''
        self._colors = config['color-codes']
        self._color_names = config['color-names']
        self._no_leds = len(self._colors[0])
        self._counter = 0
        self._port = config['gpio-port']
        
        self.initialize()

    def initialize(self):
        '''
        Use this method to reinitialize the connection to the neopixels when an exception occurs.
        '''
        try:
            self._pixels = Adafruit_NeoPixel(self._no_leds, self._port, 800000, 10, False, 255)
            self._pixels.begin()
            self._failed = False
        except Exception as error:
            self._failed = True

    def update(self):
        '''
        Returns a dictionary containing 2 keys:
        'leds': The color of all LEDs: can be one of those specified in the config parameter in the constructor.
        'id': An ever increasing counter used to identify frames.

        Can throw exceptions if something goes wrong. When it does, call `initialize` method to reinitialize the connection.
        '''
        leds_color = self._colors[self._counter % 3]
        for i in range(self._no_leds):
            color = leds_color[i]
            self._pixels.setPixelColor(i, 
            Color(color[0], color[1], color[2]))
        self._pixels.show()

        self._state = {
            'leds': self._color_names[self._counter % 3],
            'id': self._counter
        }
        self._counter += 1

        return self._state

    def failed(self):
        return self._failed

    def reset(self):
        self._failed = False

def play_and_stop(led_player, type, color):
    led_player.play(type, color)
    sleep(2)
    led_player.stop()
    led_player.join()

def generate_gifs(path, logger):
    '''
    Generate and save GIFs made of all collected frames so far.

    Key params:
    path -- Must be an absolute path and the string must have a slash symbol at the end.
    logger -- The logger used in this program.
    '''
    list_dirs = []
    mycwd = os.getcwd()
    os.makedirs(path, exist_ok = True)
    os.chdir(path)
    for entry in os.scandir('./'):
        if entry.is_dir():
            list_dirs.append(entry.path)
    os.makedirs('./gifs', exist_ok = True)

    images = {}
    for directory in list_dirs:
        logger.info('gathering images from ' + directory + ' dir ..')
        for file_name in os.listdir(directory):
            if file_name.endswith('.jpeg'):
                file_path = os.path.join(directory, file_name)
                if directory[2:] not in images:
                    images[directory[2:]] = []
                else:
                    images[directory[2:]].append(imageio.imread(file_path))
    
    for key, values in images.items():
        logger.info('saving ' + key + '.gif ..')
        imageio.mimsave('./gifs/' + key + '.gif', values)
    
    os.chdir(mycwd)

def main(manager, sync_manager, kill_event):
     # initiate logging
    os.makedirs('data/logging', exist_ok = True)
    log_queue = sync_manager.Queue(0)
    logger_sub = SUBLogger(log_queue)
    if logger_sub.failed is True:
        print('logging configuration file not found')
        return

    logger_sub.start()
    logger = PUBLogger(log_queue)

    # initiate connection to GoPiGo3
    # and load configuration data
    try:
        robot = EasyGoPiGo3()
        led_player = PlayGoPiGo3LED(robot)
        led_player.start()
        threads.append(led_player)

        config = open('qa_config.json')
        settings = json.load(config)
    except FileNotFoundError as error:
        logger.critical(str(error), exc_info = 1)
        play_and_stop(led_player, 'solid', RED)
        logger_sub.stop()
        return
    except (IOError, OSError, gopigo3.FirmwareVersionError, Exception)  as error:
        logger.critical(str(error), exc_info = 1)
        logger_sub.stop()
        return

    # create the LED switcher object
    led_switcher_settings = settings['led-switcher']
    led_changer = manager.LEDChanger(led_switcher_settings)

    # check if instantiating a neopixel object has failed
    if led_changer.failed() is True:
        logger.critical('failed instantiating LEDChanger class (probably lack of perms)')
        play_and_stop(led_player, 'solid', ORANGE)
        logger_sub.stop()
        return
    
    # create a button
    program_settings = settings['program']
    button = robot.init_button_sensor(program_settings['gopigo3-button'])

    # configuration stuff
    camera_settings = settings['camera']
    qa_settings = settings['qa']
    ttr = program_settings['time-to-run']
    save_frames = program_settings['save-frames']
    frames_dir = program_settings['frames-dir']

    # create a queue for frames and start the camera
    queue = sync_manager.Queue(0)
    producer = manager.CameraSource(queue, camera_settings, log_queue, frame_callback = led_changer)
    if producer.get_attr('failed') is True:
        play_and_stop(led_player, 'breathe', MAGENTA)
        return

    # start the camera, but before that, pause the recording
    producer.set_attr('pause', True)
    producer.start()
    threads.append(producer)

    # start the consumers which go through the frames
    # and determine if the test has failed or not
    consumers = []
    for i in range(2):
        c = manager.GiggleBotQAValidation(queue, 
            qa_settings, 
            log_queue, 
            save_images_for_debugging = save_frames,
            path_for_images = frames_dir)
        consumers.append(c)
        c.start()

    # set the WiFI LED to green to signify that the app is ready for QA tests
    logger.info('turn on WiFi LED to signify that the app is ready')
    robot.set_led(robot.LED_WIFI, 0, 255, 0)

    # do it indefinitely until a signal is caught
    testId = 0
    while not kill_event.is_set():
        sleep(0.5)

        # if the button is pressed, then a QA test is started
        if button.read() == PRESSED:
            logger.info('start qa test w/ ID=' + str(testId))
            
            if save_frames is True:
                try:
                    shutil.rmtree(frames_dir)
                    logger.debug('removed ' + frames_dir + ' tree')
                except OSError:
                    logger.warn(frames_dir + ' dir not found')

            # resume recording frames
            producer.set_attr('pause', False)
            start = time()
            led_player.play('breathe', BLUE)

            # while the test is running,
            # check if the consumers report a failed test
            # or if the producer breaks
            failed = False
            failed_producer = False
            stop_event = False
            while start + ttr >= time():
                if True in map(lambda x: x.get_attr('failed_qa'), consumers):
                    failed = True
                    break                
                elif producer.get_attr('failed') is True:
                    failed_producer = True
                    break
                elif kill_event.is_set() is True:
                    stop_event = True
                    break
                else:
                    sleep(0.05)
            end_time = time() - start

            # stop the script if a signal is caught
            if stop_event is True:
                break

            # stop the whole show if the producer
            # encountered a critical situation
            if failed_producer is True:
                led_player.play('breathe', MAGENTA)
                sleep(2)
                break

            # pause the recording and flush the queue
            producer.set_attr('pause', True)
            while queue.qsize() > 0:
                queue.get()

            # generate GIFs if asked for
            if save_frames is True:
                try:
                    generate_gifs(frames_dir, logger)
                except Exception as err:
                    logger.error(str(err), exc_info = 1)

            # conclude the test by setting the appropriate color
            if failed is True:
                led_player.play('solid', RED)
                logger.info('qa test #' + str(testId) + ' failed after ' + str(int(end_time)) + ' seconds')
            else:
                led_player.play('solid', GREEN)
                logger.info('qa test #' + str(testId) + ' succeeded after ' + str(int(end_time)) + ' seconds')
            testId += 1

            # wait a little to be sure the rest of the validating processes
            # have finished processing their remaining frames (even though the queue is emptied)
            sleep(0.5)
            
            # reset the consumers
            for c in consumers:
                logger.debug('reset failed_qa attribute for consumer ' + str(c))
                c.set_attr('failed_qa', False)

    # if a signal is caught, gracefully exit by shutting down all threads/processes
    for thread in threads + consumers:
        thread.stop()
        thread.join()
    # stop the WiFi LED to signify the end of the program
    logger.info('turn off WiFi LED due to reaching end of program')
    robot.set_led(robot.LED_WIFI, 0, 0, 0)
    logger_sub.stop()

if __name__ == "__main__":
    def exit_gracefully(signum, 
            frame, 
            kill_event):
        # allow to gracefully exit only when all threads are already running
        if signum in [1, 2, 3, 15]:
            kill_event.set()

    # signal configuration stuff
    kill_event = Event()
    bound_exit_gracefully = lambda signum, frame: exit_gracefully(signum, 
        frame,
        kill_event)

    catchable_sigs = set(signal.Signals) - {signal.SIGKILL, signal.SIGSTOP}
    for sig in catchable_sigs:
        signal.signal(sig, bound_exit_gracefully)  # Substitute handler of choice for `print`

    # multiprocessing configuration
    (MyManager, sync_manager) = gbtest.prepare_manager()
    MyManager.register(LEDChanger.__name__, LEDChanger, LEDChangerProxy)

    # start the proxy to handle processes
    with MyManager() as manager:
        main(manager, sync_manager, kill_event)

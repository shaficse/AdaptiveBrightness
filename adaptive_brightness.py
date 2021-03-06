import cv2
import numpy as np
from subprocess import call, check_output
import time
import datetime
import psutil
import tensorflow as tf

tf.enable_eager_execution()


def to_range(value, minimum, maximum):
    return min(max(value, minimum), maximum)


def log(output):
    print output


class LightSensor:

    def __init__(self, camera_port=0):
        self.camera = None
        self.camera_port = camera_port
        self.enabled = False

    @staticmethod
    def __set_auto_exposure(auto_exposure_on):
        call(["v4l2-ctl", "--set-ctrl", "exposure_auto_priority=" + str(int(auto_exposure_on))])

    def enable(self):
        try:
            self.camera = cv2.VideoCapture(self.camera_port)
            LightSensor.__set_auto_exposure(False)
            self.enabled = True
        except Exception:
            self.enabled = False

    def get(self):
        if self.enabled:
            try:
                _, frame = self.camera.read()
                yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV)
                channels = cv2.split(yuv)
                return np.mean(channels[0])
            except Exception:
                return 0.0
        else:
            return 0.0

    def disable(self):
        LightSensor.__set_auto_exposure(True)
        if self.enabled:
            self.camera.release()


class Backlight:

    def __init__(self):
        pass

    def set_brightness(self, percentage):
        try:
            percentage = int(to_range(round(percentage), 0, 100))
            log("Setting brightness to " + str(percentage))
            call(['gdbus', 'call', '--session', '--dest', 'org.gnome.SettingsDaemon.Power',
                  '--object-path', '/org/gnome/SettingsDaemon/Power', '--method', 'org.freedesktop.DBus.Properties.Set',
                  'org.gnome.SettingsDaemon.Power.Screen', 'Brightness', '<int32 ' + str(percentage) + '>'])
        except Exception:
            pass

    def get_brightness(self):
        try:
            output = check_output(['gdbus', 'call', '--session', '--dest', 'org.gnome.SettingsDaemon.Power',
                                 '--object-path', '/org/gnome/SettingsDaemon/Power', '--method',
                                 'org.freedesktop.DBus.Properties.Get',
                                 'org.gnome.SettingsDaemon.Power.Screen', 'Brightness'])

            number = ""

            for char in output:
                if char.isdigit():
                    number += char

            return int(number)
        except Exception:
            return 0



class Battery:

    def __init__(self):
        pass

    def get_percent(self):
        return psutil.sensors_battery().percent

    def is_plugged_in(self):
        return psutil.sensors_battery().power_plugged


class Clock:

    def __init__(self):
        pass

    def get_as_float(self):
        time_of_day = self.get()
        return time_of_day.hour + time_of_day.minute / 60.0

    def get(self):
        return datetime.datetime.now().time()


class LowPassFilter:

    def __init__(self, filter_coef):
        self.filter_coef = to_range(filter_coef, 0, 1)
        self.last_value = 0.0

    def filter(self, value):
        self.last_value = self.filter_coef * self.last_value + (1 - self.filter_coef) * value
        return self.last_value


class AdaptiveBrightness:

    def __init__(self, light_sensor=LightSensor(), backlight=Backlight()):
        self.light_sensor = light_sensor
        self.backlight = backlight

    def get_light(self):
        self.light_sensor.enable()
        light = self.light_sensor.get()
        log("Read light as " + str(int(round(light))))
        self.light_sensor.disable()
        return light

    def set_brightness(self, percentage):
        self.backlight.set_brightness(percentage)


class SimpleAdaptiveBrightness(AdaptiveBrightness):

    def __init__(self, brightness_compensation, change_threshold=6, light_sensor=LightSensor(), backlight=Backlight()):
        AdaptiveBrightness.__init__(self, light_sensor, backlight)
        self.brightness_compensation = brightness_compensation
        self.last_change = -1
        self.change_threshold = change_threshold

    def run(self):
        light = self.get_light()
        if self.last_change == -1 or abs(light - self.last_change) > self.change_threshold:
            self.set_brightness(light * self.brightness_compensation)
            self.last_change = light


class MLAdaptiveBrightness(AdaptiveBrightness):

    def __init__(self, change_threshold=6, light_sensor=LightSensor(), backlight=Backlight()):
        AdaptiveBrightness.__init__(self, light_sensor, backlight)
        self.change_threshold = change_threshold
        self.last_change = -1
        self.data = []
        self.learning_rate = 0.01
        self.num_steps = 10
        self.batch_size = 10
        self.my_optimizer = tf.train.GradientDescentOptimizer(learning_rate=self.learning_rate)
        self.my_optimizer = tf.contrib.estimator.clip_gradients_by_norm(self.my_optimizer, 5.0)
        self.model = tf.keras.models.load_model(
            "model",
            custom_objects=None,
            compile=False
        )
        self.model.compile(optimizer=self.my_optimizer, loss=tf.keras.losses.mean_squared_error)
        self.last_brightness = self.backlight.get_brightness()

    def run(self):
        light = self.get_light()
        current_brightness = self.backlight.get_brightness()
        if current_brightness != self.last_brightness:
            self.learn(self.last_change, current_brightness)
            self.last_brightness = self.backlight.get_brightness()
        if self.last_change == -1 or abs(light - self.last_change) > self.change_threshold:
            self.set_brightness(self.model.predict(np.array([light]))[0][0])
            self.last_change = light
            self.last_brightness = self.backlight.get_brightness()

    def learn(self, light, brightness):
        remove_list = []
        for value in self.data:
            if value[0] == light:
                remove_list.append(value)
        for value in remove_list:
            self.data.remove(value)
        self.data.append([light, brightness])
        features = np.array([x[0] for x in self.data])
        labels = np.array([x[1] for x in self.data])
        self.model.fit(features, labels, batch_size=self.batch_size, epochs=self.num_steps)
        tf.keras.models.save_model(
            self.model,
            "model",
            overwrite=True,
            include_optimizer=False
        )


if __name__ == "__main__":
    adaptive_brightness = MLAdaptiveBrightness()
    while True:
        adaptive_brightness.run()
        time.sleep(6)

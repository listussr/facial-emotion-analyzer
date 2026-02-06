from collections import OrderedDict
import time
from typing import Optional, Tuple, Any, Dict
import logging
import threading


class TTLBuffer(object):
    def __init__(self, ttl: int, *, buffer_name: str = ""):
        """
        #### Буфер для хранения данных с заданным временем жизни.
        
        :param ttl: Время жизни элемента в буфере.
        :type ttl: int
        :param buffer_name: Название буфера для удобства логирования (Optional).
        :type buffer_name: str
        """
        self.buffer = OrderedDict()
        self.ttl = ttl
        self.buffer_name = buffer_name

    def cleanup(self):
        """
        #### Очистка буфера от данных с истёкшим сроком жизни.
        """
        now = time.time()
        keys = []
        for key, (ts, _) in self.buffer.items():
            if now - ts > self.ttl:
                keys.append(key)
            else:
                break

        for key in keys:
            del self.buffer[key]

        if len(keys) > 0:
            logging.info(f"From buffer {self.buffer_name} deleted {len(keys)} objects")

    def insert(self, key: Any, value: Any):
        """
        #### Вставка данных в буфер.
        
        :param key: Ключ для доступа к элементу.
        :type key: Any
        :param value: Значение для вставки.
        :type value: Any
        """
        now = time.time()
        self.buffer[key] = (now, value)
        self.buffer.move_to_end(key)

    def delete(self, key: Any):
        """
        #### Удаление данных по ключу. 
                
        :param key: Значение ключа.
        :type key: Any
        """
        item = self.buffer.pop(key, None)
        if item is None:
            return None
        _, value = item
        return value

    def get(self, key: Any):
        """
        #### Получение данных из буфера по ключу.
        
        :param key: Значение ключа.
        :type key: Any
        """
        item = self.buffer.get(key)
        if item is None:
            return None
        return item[1]

    def __contains__(self, key: Any):
        """
        #### Проверка на наличие данных с заданным ключом.
        
        :param key: Значение ключа.
        :type key: Any
        """
        return key in self.buffer


class SyncBuffer(object):
    def __init__(self, ttl_frames: int, ttl_annotations: int):
        """
        #### Буфер синхронизации кадров и аннотаций.
        
        :param ttl_frames: Время жизни кадра в секундах.
        :type ttl_frames: int
        :param ttl_annotations: Время жизни аннотации в секундах.
        :type ttl_annotations: int
        """
        self._frames_buffer = TTLBuffer(ttl_frames, buffer_name="frames")
        self._annotation_buffer = TTLBuffer(ttl_annotations, buffer_name="annotations")
        self._lock = threading.Lock()

    def cleanup(self):
        """
        #### Очистка данных из буферов.
        """
        with self._lock:
            self._annotation_buffer.cleanup()
            self._frames_buffer.cleanup()

    def add_annotation(self, frame_id: Any, annotation: Dict) -> Optional[Tuple[Dict, Dict]]:
        """
        #### Добавление в буфер аннотации.
        
        :param frame_id: Иденфтификатор кадра для нахождения соответствия между кадром и аннотацией.
        :type frame_id: Any
        :param annotation: Аннотация для добавления в буфер.
        :type annotation: Dict
        :return: Пара (кадр, аннотация) в случае, если найдено соответствие по идентификатору.
        :rtype: Tuple[Dict, Dict] | None
        """
        with self._lock:
            self._annotation_buffer.insert(frame_id, annotation)

            if frame_id not in self._frames_buffer:
                return None

            annotation = self._annotation_buffer.delete(frame_id)
            frame = self._frames_buffer.delete(frame_id)

            if annotation is None or frame is None:
                return None

            return frame, annotation

    def add_frame(self, frame_id: Any, frame: Dict) -> Optional[Tuple[Dict, Dict]]:
        """
        #### Добавление в буфер кадр.
        
        :param frame_id:  Иденфтификатор кадра для нахождения соответствия между кадром и аннотацией.
        :type frame_id: Any
        :param frame: Кадр для добавления в буфер.
        :type frame: Dict
        :return: Пара (кадр, аннотация) в случае, если найдено соответствие по идентификатору.
        :rtype: Tuple[Dict, Dict] | None
        """
        with self._lock:
            self._frames_buffer.insert(frame_id, frame)

            if frame_id not in self._annotation_buffer:
                return None

            annotation = self._annotation_buffer.delete(frame_id)
            frame = self._frames_buffer.delete(frame_id)

            if annotation is None or frame is None:
                return None

            return frame, annotation

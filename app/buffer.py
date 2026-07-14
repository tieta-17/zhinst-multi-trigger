import numpy as np

class RollingBuffer:
    def __init__(self, buffer_length):
        self.buffer_length = buffer_length;
        self.buffers = []
        self.write_index = 0;
        for i in range(0, self.buffer_length):
            self.buffers.append(np.zeros(1))
    
    def add_sub_buffer(self, sub_buffer):
        self.buffers[self.write_index] = sub_buffer;
        self.write_index += 1;
        if(self.write_index == self.buffer_length):
            self.write_index = 0;
            
    def get_full_buffer(self):
        ret_array = np.empty(0)
        for i in range(self.write_index, self.write_index + self.buffer_length):
            n = i % self.buffer_length;
            ret_array = np.append(ret_array, self.buffers[n])
        return ret_array
    def get_x_buffers(self, x):
        ret_array = np.empty(0)
        for i in range(self.write_index - x, self.write_index):
            if(i < 0):
                n = self.buffer_length + i
            else:
                n = i
            ret_array = np.append(ret_array, self.buffers[n])
        return ret_array
        
    def print_full(self):
        print("\nbuffer length:", self.buffer_length)
        print("write index:", self.write_index)
        print(self.buffers,"\n")

class Channel:
    def __init__(self, demod_idx, n):
        self.demod_idx = demod_idx
        self.rb_x = RollingBuffer(n)
        self.rb_y = RollingBuffer(n)
        self.rb_r = RollingBuffer(n)
        self.rb_phase = RollingBuffer(n)
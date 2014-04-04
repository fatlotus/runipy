from __future__ import print_function

try:
    # python 2
    from Queue import Empty
except:
    # python 3
    from queue import Empty

import platform
from time import sleep
import logging

from IPython.nbformat.current import read, write, NotebookNode
from IPython.kernel.inprocess.manager import InProcessKernelManager


class NotebookError(Exception):
    pass


class NotebookRunner(object):
    # The kernel communicates with mime-types while the notebook
    # uses short labels for different cell types. We'll use this to
    # map from kernel types to notebook format types.

    MIME_MAP = {
        'image/jpeg': 'jpeg',
        'image/png': 'png',
        'text/plain': 'text',
        'text/html': 'html',
        'text/latex': 'latex',
        'application/javascript': 'html',
    }

    def __init__(self):
        self.km = InProcessKernelManager()
        self.km.start_kernel()

        if platform.system() == 'Darwin':
            # There is sometimes a race condition where the first
            # execute command hits the kernel before it's ready.
            # It appears to happen only on Darwin (Mac OS) and an
            # easy (but clumsy) way to mitigate it is to sleep
            # for a second.
            sleep(1)

        self.kc = self.km.client()
        self.kc.start_channels()

        self.shell = self.kc.shell_channel
        self.iopub = self.kc.iopub_channel
        self.kc.kernel.shell.enable_matplotlib('inline')

        self.shell.execute(
            "%matplotlib inline\n"
            "from matplotlib import pyplot as plt\n"
            "plt.figure()\n"
            "x = range(10)\n"
            "y = range(20, 30)\n"
            "plt.plot(x, y)\n"
            "plt.show()"
        )
        print("Result: {!r}".format(self.shell.get_msg()))

        try:
            while True:
                msg = self.iopub.get_msg(timeout=0)
        except Empty:
            pass

    def __del__(self):
        self.kc.stop_channels()
        self.km.shutdown_kernel()

    def run_cell(self, cell, autosave):
        '''
        Run a notebook cell and update the output of that cell in-place.
        '''
        logging.info('Running cell:\n%s\n', cell.input)
        self.shell.execute(cell.input)

        cell['outputs'] = []
        while True:
            try:
                msg = self.iopub.get_msg(timeout=1)
                if msg['msg_type'] == 'status':
                    if msg['content']['execution_state'] == 'idle':
                        break
            except Empty:
                pass

            content = msg['content']
            msg_type = msg['msg_type']

            out = NotebookNode(output_type=msg_type)

            if 'execution_count' in content:
                cell['prompt_number'] = content['execution_count'] - 1
                out.prompt_number = content['execution_count'] - 1

            if msg_type in ['status', 'pyin']:
                continue
            elif msg_type == 'stream':
                out.stream = content['name']
                out.text = content['data']
                #print(out.text, end='')
            elif msg_type in ('display_data', 'pyout'):
                for mime, data in content['data'].items():
                    try:
                        attr = self.MIME_MAP[mime]
                    except KeyError:
                        raise NotImplementedError('unhandled mime type: %s' % mime)

                    setattr(out, attr, data)
                #print(data, end='')
            elif msg_type == 'pyerr':
                out.ename = content['ename']
                out.evalue = content['evalue']
                out.traceback = content['traceback']

                #logging.error('\n'.join(content['traceback']))
            else:
                raise NotImplementedError('unhandled iopub message: %s' % msg_type)
            
            cell['outputs'].append(out)
            if autosave:
                self.save_notebook(autosave)

        reply = self.shell.get_msg()
        status = reply['content']['status']
        if status == 'error':
            logging.info('Cell raised uncaught exception: \n%s', '\n'.join(reply['content']['traceback']))
            raise NotebookError()
        else:
            logging.info('Cell returned')

    def iter_code_cells(self):
        '''
        Iterate over the notebook cells containing code.
        '''
        for ws in self.nb.worksheets:
            for cell in ws.cells:
                if cell.cell_type == 'code':
                    yield cell


    def run_notebook(self, nb_in, skip_exceptions=False, autosave=None):
        '''
        Run all the cells of a notebook in order and update
        the outputs in-place.

        If ``skip_exceptions`` is set, then if exceptions occur in a cell, the
        subsequent cells are run (by default, the notebook execution stops).
        '''
        self.nb = read(open(nb_in), 'json')
        
        for cell in self.iter_code_cells():
            cell['outputs'] = []
            if 'prompt_number' in cell:
                del cell['prompt_number']
        
        if autosave is not None:
            self.save_notebook(autosave)
        
        for cell in self.iter_code_cells():
            try:
                self.run_cell(cell, autosave = autosave)
            except NotebookError:
                if not skip_exceptions:
                    raise
            if autosave is not None:
                self.save_notebook(autosave)

    def save_notebook(self, nb_out):
        logging.info('Saving to %s', nb_out)
        write(self.nb, open(nb_out, 'w'), 'json')


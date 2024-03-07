from collections import OrderedDict
import numpy as np
from datetime import datetime


def parse_lab_notebook(hdf):
    """Return compiled data from the lab notebook in the given hdf.

        Parameters:
        -----------
        hdf : HDF5 file (needs to have a labnotebook field)

        Returns:
        --------
        notebook : dict
            Contains one key:value pair per sweep. Each value is a list 
            containing one metadata dict for each channel in the sweep. 
            For example::

            notebook[sweep_id][channel_id][metadata_key]

        """

    # collect all lab notebook entries
    sweep_entries = OrderedDict()
    tp_entries = []
    device = list(hdf['general/devices'].keys())[0].split('_',1)[-1]
    #nb_keys = hdf['general']['labnotebook'][device]['numericalKeys'][0]
    nb_keys = hdf['general']['labnotebook'][device]['numericalKeys'].asstr()[0]
    nb_fields = OrderedDict([(k, i) for i,k in enumerate(nb_keys)])

    # convert notebook to array here, otherwise we incur the decompression cost for the entire
    # dataset every time we try to access part of it. 
    nb = np.array(hdf['general']['labnotebook'][device]['numericalValues'])

    # EntrySourceType field is needed to distinguish between records created by TP vs sweep
    entry_source_type_index = nb_fields.get('EntrySourceType', None)
    
    nb_iter = iter(range(nb.shape[0]))  # so we can skip multiple rows from within the loop
    for i in nb_iter:
        rec = nb[i]
        sweep_num = rec[0,0]

        is_tp_record = False
        is_sweep_record = False

        # ignore records that were generated by test pulse
        # (note: entrySourceType is nan if an older pxp is re-exported to nwb using newer MIES)
        if entry_source_type_index is not None and not np.isnan(rec[entry_source_type_index][0]):
            if rec[entry_source_type_index][0] == 0:
                is_sweep_record = True
            else:
                is_tp_record = True
        #elif i < nb.shape[0] - 1:
            # Older files may be missing EntrySourceType. In this case, we can identify TP blocks
            # as two records containing a "TP Peak Resistance" value in the first record followed
            # by a "TP Pulse Duration" value in the second record.
            #tp_peak = rec[nb_fields['TP Peak Resistance']]
            #if any(np.isfinite(tp_peak)):
                #tp_dur = nb[i+1][nb_fields['TP Pulse Duration']]
                #if any(np.isfinite(tp_dur)):
                    #next(nb_iter)
                    #is_tp_record = True
            #if not is_tp_record:
                #is_sweep_record = np.isfinite(sweep_num)

        if is_tp_record:
            rec = np.array(rec)
            next(nb_iter)
            rec2 = np.array(nb[i+1])
            mask = ~np.isnan(rec2)
            rec[mask] = rec2[mask]
            tp_entries.append(rec)

        elif is_sweep_record:
            sweep_num = int(sweep_num)
            # each sweep gets multiple nb records; for each field we use the last non-nan value in any record
            if sweep_num not in sweep_entries:
                sweep_entries[sweep_num] = np.array(rec)
            else:
                mask = ~np.isnan(rec)
                sweep_entries[sweep_num][mask] = rec[mask]

    for swid, entry in sweep_entries.items():
        # last column is "global"; applies to all channels
        mask = ~np.isnan(entry[:,8])
        entry[mask] = entry[:,8:9][mask]

        # first 4 fields of first column apply to all channels
        entry[:4] = entry[:4, 0:1]

        # async AD fields (notably used to record temperature) appear
        # only in column 0, but might move to column 8 later? Since these
        # are not channel-specific, we'll copy them to all channels
        for i,k in enumerate(nb_keys):
            if not k.startswith('Async AD '):
                continue
            entry[i] = entry[i, 0]

        # convert to list-o-dicts
        meta = []
        for i in range(entry.shape[1]):
            tm = entry[:, i]
            meta.append(OrderedDict([(nb_keys[j], (None if np.isnan(tm[j]) else tm[j])) for j in range(len(nb_keys))]))
        sweep_entries[swid] = meta

    # Load textual keys in a similar way 
    #text_nb_keys = hdf['general']['labnotebook'][device]['textualKeys'][0]
    text_nb_keys = hdf['general']['labnotebook'][device]['textualKeys'].asstr()[0]
    text_nb_fields = OrderedDict([(k, i) for i,k in enumerate(text_nb_keys)])
    #text_nb = np.array(hdf['general']['labnotebook'][device]['textualValues'])
    text_nb = np.array(hdf['general']['labnotebook'][device]['textualValues']).astype(str)
    entry_source_type_index = text_nb_fields.get('EntrySourceType', None)

    for rec in text_nb:
        if entry_source_type_index is None:
            # older nwb files lack EntrySourceType; fake it for now
            source_type = 0
        else:
            try:
                source_type = int(rec[entry_source_type_index, 0])
            except ValueError:
                # No entry source type recorded here; skip for now.
                continue

        if source_type != 0:
            # Select only sweep records for now.
            continue

        try:
            sweep_id = int(rec[0,0])
        except ValueError:
            # Not sure how to handle records with no sweep ID; skip for now.
            continue
        sweep_entry = sweep_entries[sweep_id]

        for k,i in text_nb_fields.items():                    
            for j, val in enumerate(rec[i, :-1]):
                if k in sweep_entry[j]:
                    # already have a value here; don't overwrite.
                    continue

                if val == '':
                    # take value from last column if this one is empty
                    val == rec[i, -1]
                if val == '':
                    # no value here; skip.
                    continue
                
                sweep_entry[j][k] = val

    return sweep_entries
    #self._tp_notebook = tp_entries
    #self._notebook_keys = nb_fields
    #self._tp_entries = None

def igorpro_date(timestamp):
    """Convert an IgorPro timestamp (seconds since 1904-01-01) to a datetime
    object.
    """
    dt = datetime(1970,1,1) - datetime(1904,1,1)
    return datetime.utcfromtimestamp(timestamp) - dt

def parse_stim_wave_note(rec_notebook):
    """Return (version, epochs) from the stim wave note of the labnotebook associated with a recording.

    Paramenters:
    ------------
    rec_notebook : dict
        A labnotebook dict for a recording, as returned by parse_lab_notebook(hdf)[sweep_id][channel]

    Returns:
    --------
    (version, epochs) : tuple
        version is an int, epochs is a list of dicts

    """

    sweep_count = int(rec_notebook['Set Sweep Count'])
    wave_note = rec_notebook['Stim Wave Note']
    lines = wave_note.split('\n')
    version = [line for line in lines if line.startswith('Version =')]
    if len(version) == 0:
        version = 0
    else:
        version = float(version[0].rstrip(';').split(' = ')[1])
    epochs = []
    for line in lines:
        if not line.startswith('Sweep = %d;' % sweep_count):
            continue
        epoch = dict([part.split(' = ') for part in line.split(';') if '=' in part])
        epochs.append(epoch)
        
    return version, epochs


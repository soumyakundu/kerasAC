#compute gradient x input for tensorflow models.
import argparse
import pdb 
from deeplift.conversion import kerasapi_conversion as kc
import numpy as np
import pyBigWig
from .config import args_object_from_args_dict

def parse_args():
    parser=argparse.ArgumentParser(description="get gradient x input for a model")
    parser.add_argument("--model_hdf5")
    parser.add_argument("--w0",nargs="+",type=float)
    parser.add_argument("--w1",nargs="+",type=float)
    parser.add_argument("--data_path")
    parser.add_argument("--interpret_chroms",nargs="*") 
    parser.add_argument("--interpretation_outf")
    parser.add_argument("--flank",default=None,type=int)
    parser.add_argument("--method",choices=['gradxinput','deeplift'],default="deeplift")
    parser.add_argument('--batch_size',type=int,help='batch size to use to make model predictions',default=50)
    parser.add_argument('--ref_fasta',default="/srv/scratch/annashch/deeplearning/form_inputs/code/hg19.genome.fa")
    parser.add_argument('--background_freqs',default=None)
    parser.add_argument('--center_on_summit',default=False,action='store_true',help="if this is set to true, the peak will be centered at the summit (must be last entry in bed file or hammock) and expanded args.flank to the left and right")
    parser.add_argument('--task_id',type=int)
    parser.add_argument('--squeeze_input_for_gru',default=False,action='store_true')
    parser.add_argument('--assembly',default='hg19')
    parser.add_argument('--chromsizes',default='/mnt/data/annotations/by_release/hg19.GRCh37/hg19.chrom.sizes')
    return parser.parse_args()


def get_deeplift_function(args):
    # convert to deeplift model and get scoring function
    deeplift_model = kc.convert_model_from_saved_files(args.model_hdf5,verbose=False)

    #get the deeplift score with respect to the logit 
    score_func = deeplift_model.get_target_contribs_func(
        find_scores_layer_idx=0,
        target_layer_idx=-2)
    return score_func

def get_deeplift_references(args):
    if args.background_freqs==None:
        # use a 40% GC reference
        input_references = [np.array([0.3, 0.2, 0.2, 0.3])[None, None, None, :]]
    else:
        input_references=[np.array(args.background_freqs)[None,None,None,:]]
    return input_references

def add_bigwig_header(bw,assembly):
    if assembly=='hg19':
        bw.addHeader([('chr1',249250621),
                      ('chr2',243199373),                      
                      ('chr3',198022430),
                      ('chr4',191154276),
                      ('chr5',180915260),
                      ('chr6',171115067),
                      ('chr7',159138663),
                      ('chr8',146364022),
                      ('chr9',141213431),
                      ('chr10',135534747),
                      ('chr11',135006516),
                      ('chr12',133851895),
                      ('chr13',115169878),
                      ('chr14',107349540),
                      ('chr15',102531392),
                      ('chr16',90354753),
                      ('chr17',81195210),
                      ('chr18',78077248),
                      ('chr19',59128983),
                      ('chr20',63025520),
                      ('chr21',48129895),
                      ('chr22',51304566),
                      ('chrX',155270560),
                      ('chrY',59373566)])
        return bw
    else:
        raise Exception("implement bigWig header for this assembly!")
def get_chromsizes(f):
    data=open(f,'r').read().strip().split('\n')
    chromsize_dict=dict()
    for line in data:
        tokens=line.split()
        chromsize_dict[tokens[0]]=int(tokens[1])
    return chromsize_dict

def get_deeplift_scores_bed(args,score_func,input_references):
    import pysam
    import pandas as pd
    num_generated=0
    ref=pysam.FastaFile(args.ref_fasta) 
    data=pd.read_csv(args.data_path,header=None,sep='\t')
    ltrdict = {'a':[1,0,0,0],'c':[0,1,0,0],'g':[0,0,1,0],'t':[0,0,0,1], 'n':[0,0,0,0],'A':[1,0,0,0],'C':[0,1,0,0],'G':[0,0,1,0],'T':[0,0,0,1],'N':[0,0,0,0]}
    #iterate through batches and one-hot-encode on the fly
    num_entries=data.shape[0]
    bw=pyBigWig.open(args.interpretation_outf,'w')
    bw=add_bigwig_header(bw,args.assembly)
    chromsize_dict=get_chromsizes(args.chromsizes)
    while num_generated < num_entries:
        print(str(num_generated))
        start_index=num_generated
        end_index=min([num_entries,start_index+args.batch_size])
        seqs=[]
        chroms=[]
        start_vals=[]
        end_vals=[]
        for i in range(start_index,end_index):
            cur_row=data.iloc[i]
            chrom=cur_row[0]
            start_val=cur_row[1]
            end_val=cur_row[2]
            if args.center_on_summit==True:
                summit_offset=int(cur_row[-1])
                summit_pos=start_val+summit_offset
                start_val=summit_pos - args.flank
                end_val=summit_pos+args.flank
            if start_val<1:
                start_val=1
                end_val=1+2*args.flank
            if end_val>chromsize_dict[chrom]:
                end_val=chromsize_dict[chrom]-1
                start_val=end_val-2*args.flank 
            try:
                seq=ref.fetch(chrom,start_val,end_val)
                seqs.append(seq)
                chroms.append(chrom)
                start_vals.append(start_val)
                end_vals.append(end_val) 
            except:
                continue
        seqs=np.array([[ltrdict.get(x,[0,0,0,0]) for x in seq] for seq in seqs])
        if (args.squeeze_input_for_gru==False):
            #expand dimension of 1
            x=np.expand_dims(seqs,1)
        else:
            x=seqs

        cur_scores = score_func(
            task_idx=args.task_id,
            input_data_list=[x],
            batch_size=x.shape[0],
            progress_update=None,
            input_references_list=input_references)*x        
        for i in range(len(cur_scores)):
            base_scores=np.ndarray.tolist(np.sum(cur_scores[i].squeeze(),axis=1))
            try:
                bw.addEntries(chroms[i],start_vals[i],values=base_scores,span=1,step=1)
            except:
                continue
        num_generated+=(end_index-start_index)
    bw.close()
    return 
def get_gradxinput_scores_bed(args,normed_grad):
    import pysam
    import pandas as pd
    num_generated=0
    ref=pysam.FastaFile(args.ref_fasta) 
    data=pd.read_csv(args.data_hammock,header=None,sep='\t')
    ltrdict = {'a':[1,0,0,0],'c':[0,1,0,0],'g':[0,0,1,0],'t':[0,0,0,1], 'n':[0,0,0,0],'A':[1,0,0,0],'C':[0,1,0,0],'G':[0,0,1,0],'T':[0,0,0,1],'N':[0,0,0,0]}
    #iterate through batches and one-hot-encode on the fly
    num_entries=data.shape[0]
    outf=open(args.interpretation_outf,'w')
    while num_generated < num_entries:
        print(str(num_generated))
        start_index=num_generated
        end_index=min([num_entries,start_index+args.batch_size])
        seqs=[]
        chroms=[]
        start_vals=[]
        end_vals=[]
        for i in range(start_index,end_index):
            cur_row=data.iloc[i]
            chrom=cur_row[0]
            start_val=cur_row[1]
            end_val=cur_row[2]
            if args.center_on_summit==True:
                summit_offset=int(cur_row[-1])
                summit_pos=start_val+summit_offset
                start_val=summit_pos - args.flank
                end_val=summit_pos+args.flank
                if start_val<1:
                    start_val=1
                    end_val=1+2*args.flank 
            try:
                seq=ref.fetch(chrom,start_val,end_val)
                seqs.append(seq)
                chroms.append(chrom)
                start_vals.append(start_val)
                end_vals.append(end_val) 
            except:
                continue
        seqs=np.array([[ltrdict.get(x,[0,0,0,0]) for x in seq] for seq in seqs])
        if (args.squeeze_input_for_gru==False):
            #expand dimension of 1
            x=np.expand_dims(seqs,1)
        else:
            x=seqs
        cur_scores = normed_grad*x
        pdb.set_trace()
        for i in range(len(cur_scores)):
            entry=[chroms[i],start_vals[i],end_vals[i],cur_scores[i]]
            outf.write('\t'.join([str(j) for j in entry])+'\n')
        num_generated+=(end_index-start_index)


def interpret(args):
    if type(args)==type({}):
        args=args_object_from_args_dict(args)
    if args.method=="deeplift":
        # get deeplift scores
        score_func=get_deeplift_function(args)
        input_references=get_deeplift_references(args)
        get_deeplift_scores_bed(args,score_func,input_references)
        
    elif args.method=="gradxinput":
        #calculate gradient x input 
        model=get_model(args)
        import tensorflow as tf
        grad_tensor=K.gradients(model.layers[-2].output,model.layers[0].input)
        grad_func = K.function([model.layers[0].input,K.learning_phase()], grad_tensor)
        gradient = grad_func([inputs, False])[0]
        normed_gradient = gradient-np.mean(gradient, axis=3)[:,:,:,None]
        get_gradxinput_scores_bed(args,normed_grad)
        normed_grad_times_inp = normed_gradient*inputs
    else:
        raise Exception("method must be one of 'deeplift' or 'gradxinput'")
        
def main():
    args=parse_args()
    interpret(args) 
    
if __name__=="__main__":
    main()
    

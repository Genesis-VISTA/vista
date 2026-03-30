import torch
import torch.nn as nn
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data.distributed import DistributedSampler
import numpy as np
from torch.optim import Adam
from tqdm import tqdm
import pandas as pd
import argparse
import os, math, time
from sklearn.model_selection import train_test_split


def prepare_llm_input(formula_str, comp_str, precision=2):
    """
    Convert formula + composition strings into a canonical LLM input string.

    Handles:
    - Multi-component mixtures (e.g., "AlCl3-LiCl-NaCl", "0.5-0.1-0.4")
    - Pure salts where comp_str == "Pure Salt"
    """
    components = formula_str.split("-")

    if isinstance(comp_str, str) and comp_str.strip().lower() == "pure salt":
        if len(components) != 1:
            raise ValueError(
                f'Pure Salt specified but multiple components found: {components}'
            )
        fractions = [1.0]
    else:
        fractions = [float(x) for x in comp_str.split("-")]

    if len(components) != len(fractions):
        raise ValueError("Mismatch between number of components and fractions")

    pairs = sorted(zip(components, fractions), key=lambda x: x[0])

    total = sum(f for _, f in pairs)
    if total <= 0:
        raise ValueError("Total composition must be positive")

    pairs = [(c, f / total) for c, f in pairs]

    fmt = f"{{:.{precision}f}}"
    llm_input = "; ".join(
        f"{comp}={fmt.format(frac)}"
        for comp, frac in pairs
    )

    return llm_input


def setup_distributed():
    """Initialize distributed training environment"""
    if 'RANK' in os.environ and 'WORLD_SIZE' in os.environ:
        rank = int(os.environ['RANK'])
        world_size = int(os.environ['WORLD_SIZE'])
        local_rank = int(os.environ.get('LOCAL_RANK', 0))
    else:
        rank = 0
        world_size = 1
        local_rank = 0

    if world_size > 1:
        dist.init_process_group(backend='nccl')
        torch.cuda.set_device(local_rank)

    return rank, world_size, local_rank


def cleanup_distributed():
    """Clean up distributed training"""
    if dist.is_initialized():
        dist.destroy_process_group()


def is_main_process():
    """Check if current process is the main process"""
    return not dist.is_initialized() or dist.get_rank() == 0


def save_checkpoint(model, optimizer, scheduler, epoch, best_val_metric, checkpoint_dir, is_best=False):
    """Save model checkpoint"""
    if not is_main_process():
        return

    os.makedirs(checkpoint_dir, exist_ok=True)

    model_to_save = model.module if hasattr(model, 'module') else model

    checkpoint = {
        'epoch': epoch,
        'model_state_dict': model_to_save.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'scheduler_state_dict': scheduler.state_dict() if scheduler is not None else None,
        'best_val_metric': best_val_metric,
    }

    checkpoint_path = os.path.join(checkpoint_dir, 'checkpoint_latest.pt')
    torch.save(checkpoint, checkpoint_path)
    print(f"Saved checkpoint to {checkpoint_path}")

    if is_best:
        best_path = os.path.join(checkpoint_dir, 'checkpoint_best.pt')
        torch.save(checkpoint, best_path)
        print(f"Saved best checkpoint to {best_path}")

    if (epoch + 1) % 5 == 0:
        periodic_path = os.path.join(checkpoint_dir, f'checkpoint_epoch_{epoch+1}.pt')
        torch.save(checkpoint, periodic_path)
        print(f"Saved periodic checkpoint to {periodic_path}")


def load_checkpoint(model, optimizer, scheduler, checkpoint_path, device):
    """Load model checkpoint"""
    if not os.path.exists(checkpoint_path):
        print(f"No checkpoint found at {checkpoint_path}")
        return 0, 0.0

    print(f"Loading checkpoint from {checkpoint_path}")
    checkpoint = torch.load(checkpoint_path, map_location=device)

    model_to_load = model.module if hasattr(model, 'module') else model
    missing_keys, unexpected_keys = model_to_load.load_state_dict(
        checkpoint['model_state_dict'], strict=False
    )

    if missing_keys:
        print(f"Warning: Missing keys in checkpoint: {missing_keys}")
    if unexpected_keys:
        print(f"Warning: Unexpected keys in checkpoint: {unexpected_keys}")

    if optimizer is not None:
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])

    if scheduler is not None and checkpoint.get('scheduler_state_dict') is not None:
        scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
        print(f"Loaded scheduler state")

    epoch = checkpoint.get('epoch', 0)
    best_val_metric = checkpoint.get('best_val_metric', 0.0)

    print(f"Resumed from epoch {epoch+1}, best val metric: {best_val_metric**0.5:.3f}")
    return epoch, best_val_metric**0.5


class Dataset(torch.utils.data.Dataset):

    def __init__(self, df, tokenizer, max_len=128):
        self.labels = [float(label) for label in df['melt']]
        self.texts = [tokenizer(text, padding='max_length', max_length=max_len,
                                truncation=True, return_tensors="pt")
                      for text in df['processed']]

    def __len__(self):
        return len(self.labels)

    def get_batch_labels(self, idx):
        return np.array(self.labels[idx])

    def get_batch_texts(self, idx):
        return self.texts[idx]

    def __getitem__(self, idx):
        batch_texts = self.get_batch_texts(idx)
        batch_y = self.get_batch_labels(idx)
        return batch_texts, batch_y


class ClassicalGPT(nn.Module):
    """
    LLM with a classical linear classification/regression head.
    """
    def __init__(self, HFmodel, emb_size=768, seq_len=128, n_outputs=1,
                 dropout=0.5, freeze_llm=False, task='regression'):
        super().__init__()

        if 'bert' in HFmodel.lower():
            from transformers import BertModel
            self.llm = BertModel.from_pretrained(HFmodel)
            self.model_type = 'bert'
        elif 'forge' in HFmodel.lower():
            from transformers import GPTNeoXModel
            self.llm = GPTNeoXModel.from_pretrained(HFmodel)
            self.model_type = 'gpt'

        self.dropout = nn.Dropout(dropout)
        self.freeze_llm = freeze_llm
        self.task = task

        if freeze_llm:
            for param in self.llm.parameters():
                param.requires_grad = False
            if is_main_process():
                print("LLM weights are FROZEN - will not be updated during training")
        else:
            for param in self.llm.parameters():
                param.requires_grad = True
            if is_main_process():
                print("LLM weights are UNFROZEN - will be updated during training")

        self.classifier = nn.Linear(emb_size * seq_len, n_outputs)

    def forward(self, input_id, mask):
        output = self.llm(input_ids=input_id, attention_mask=mask, return_dict=True)
        llm_out = output.last_hidden_state  # [batch, seq_len, emb_size]
        batch_size = llm_out.shape[0]
        dropout_output = self.dropout(llm_out.reshape(batch_size, -1))
        logits = self.classifier(dropout_output)
        return logits


def get_gpu_memory_info(device):
    """Get current GPU memory usage in MB"""
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated(device) / 1024**2
        reserved = torch.cuda.memory_reserved(device) / 1024**2
        max_allocated = torch.cuda.max_memory_allocated(device) / 1024**2
        return allocated, reserved, max_allocated
    return 0, 0, 0


def train(model, train_data, val_data, tokenizer, learning_rate, epochs, batch_size=4,
          use_differential_lr=True, rank=0, local_rank=0, world_size=1,
          checkpoint_dir='./checkpoints', resume_from=None, task='regression',
          warmup_epochs=0, min_lr=1e-7, log_memory_interval=10):

    train_dataset = Dataset(train_data, tokenizer)
    val_dataset = Dataset(val_data, tokenizer)

    memory_log = {'epoch': [], 'batch': [], 'allocated_mb': [], 'reserved_mb': [], 'max_allocated_mb': []}
    speed_log = {
        'epoch': [], 'epoch_time_s': [], 'train_time_s': [], 'val_time_s': [],
        'samples_per_sec': [], 'batches_per_sec': [], 'avg_batch_time_ms': []
    }

    if world_size > 1:
        train_sampler = DistributedSampler(train_dataset, num_replicas=world_size, rank=rank, shuffle=True)
        val_sampler = DistributedSampler(val_dataset, num_replicas=world_size, rank=rank, shuffle=False)
        train_dataloader = torch.utils.data.DataLoader(
            train_dataset, batch_size=batch_size, sampler=train_sampler, pin_memory=True, num_workers=1
        )
        val_dataloader = torch.utils.data.DataLoader(
            val_dataset, batch_size=batch_size, sampler=val_sampler, pin_memory=True, num_workers=1
        )
    else:
        train_dataloader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_dataloader = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size)

    use_cuda = torch.cuda.is_available()
    device = torch.device(f"cuda:{local_rank}" if use_cuda else "cpu")

    if task == 'regression':
        criterion = nn.MSELoss()
        metric_name = 'RMSE'
    else:
        criterion = nn.CrossEntropyLoss()
        metric_name = 'Accuracy'

    base_model = model.module if hasattr(model, 'module') else model
    if not base_model.freeze_llm and use_differential_lr:
        optimizer = Adam([
            {'params': base_model.llm.parameters(), 'lr': learning_rate * 0.01},
            {'params': base_model.classifier.parameters(), 'lr': learning_rate}
        ])
        if is_main_process():
            print(f"Using differential LRs: LLM={learning_rate * 0.01}, Head={learning_rate}")
    else:
        optimizer = Adam(model.parameters(), lr=learning_rate)
        if is_main_process():
            status = "frozen LLM" if base_model.freeze_llm else "end-to-end"
            print(f"Training ({status}) with LR={learning_rate}")

    warmup_steps = warmup_epochs * len(train_dataloader)
    total_steps = epochs * len(train_dataloader)

    def lr_lambda(current_step):
        if current_step < warmup_steps:
            return float(current_step) / float(max(1, warmup_steps))
        progress = float(current_step - warmup_steps) / float(max(1, total_steps - warmup_steps))
        cosine_decay = 0.5 * (1.0 + math.cos(math.pi * progress))
        return max(min_lr / learning_rate, cosine_decay)

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    if is_main_process():
        print(f"LR schedule: cosine annealing, {warmup_epochs} warmup epochs, min LR={min_lr}")

    if use_cuda:
        model = model.to(device)
        criterion = criterion.to(device)
        if is_main_process():
            torch.cuda.reset_peak_memory_stats(device)

    start_epoch = 0
    best_val_metric = float('inf') if task == 'regression' else 0.0

    if resume_from:
        start_epoch, _ = load_checkpoint(model, optimizer, scheduler, resume_from, device)
        start_epoch += 1

    for epoch_num in range(start_epoch, epochs):
        epoch_start_time = time.time()

        if world_size > 1:
            train_sampler.set_epoch(epoch_num)

        # --- Training ---
        train_start_time = time.time()
        model.train()
        total_metric_train = 0
        total_loss_train = 0
        batch_times = []
        epoch_memory_samples = []

        train_iter = tqdm(train_dataloader, desc=f"Epoch {epoch_num+1}/{epochs}") if is_main_process() else train_dataloader

        for batch_idx, (train_input, train_label) in enumerate(train_iter):
            batch_start_time = time.time()
            train_label = train_label.to(device)
            mask = train_input['attention_mask'].squeeze(1).to(device)
            input_id = train_input['input_ids'].squeeze(1).to(device)

            output = model(input_id, mask)

            if task == 'regression':
                batch_loss = criterion(output.squeeze(), train_label.float())
                total_metric_train += batch_loss.item() * train_label.size(0)
            else:
                batch_loss = criterion(output, train_label.long())
                total_metric_train += (output.argmax(dim=1) == train_label).sum().item()

            total_loss_train += batch_loss.item()

            model.zero_grad()
            batch_loss.backward()
            optimizer.step()
            scheduler.step()

            batch_time = time.time() - batch_start_time
            batch_times.append(batch_time)

            if is_main_process() and use_cuda and batch_idx % log_memory_interval == 0:
                allocated, reserved, max_allocated = get_gpu_memory_info(device)
                epoch_memory_samples.append({'batch': batch_idx, 'allocated': allocated,
                                             'reserved': reserved, 'max_allocated': max_allocated})
                memory_log['epoch'].append(epoch_num)
                memory_log['batch'].append(batch_idx)
                memory_log['allocated_mb'].append(allocated)
                memory_log['reserved_mb'].append(reserved)
                memory_log['max_allocated_mb'].append(max_allocated)

            if is_main_process() and isinstance(train_iter, tqdm):
                current_lr = scheduler.get_last_lr()[0]
                postfix = {'loss': f'{batch_loss.item():.4f}', 'lr': f'{current_lr:.2e}'}
                if use_cuda and batch_idx % log_memory_interval == 0:
                    allocated, _, _ = get_gpu_memory_info(device)
                    postfix['gpu_mb'] = f'{allocated:.0f}'
                if batch_times:
                    postfix['ms/batch'] = f'{np.mean(batch_times[-10:]) * 1000:.0f}'
                train_iter.set_postfix(postfix)

        train_time = time.time() - train_start_time

        # --- Validation ---
        val_start_time = time.time()
        model.eval()
        total_metric_val = 0
        total_loss_val = 0

        with torch.no_grad():
            for val_input, val_label in val_dataloader:
                val_label = val_label.to(device)
                mask = val_input['attention_mask'].squeeze(1).to(device)
                input_id = val_input['input_ids'].squeeze(1).to(device)

                output = model(input_id, mask)

                if task == 'regression':
                    batch_loss = criterion(output.squeeze(), val_label.float())
                    total_metric_val += batch_loss.item() * val_label.size(0)
                else:
                    batch_loss = criterion(output, val_label.long())
                    total_metric_val += (output.argmax(dim=1) == val_label).sum().item()

                total_loss_val += batch_loss.item()

        val_time = time.time() - val_start_time
        epoch_time = time.time() - epoch_start_time

        if world_size > 1:
            metrics = torch.tensor([total_loss_train, total_metric_train, total_loss_val, total_metric_val],
                                   device=device)
            dist.all_reduce(metrics, op=dist.ReduceOp.SUM)
            total_loss_train, total_metric_train, total_loss_val, total_metric_val = metrics.tolist()

        train_metric = total_metric_train / len(train_data)
        val_metric = total_metric_val / len(val_data)

        num_train_samples = len(train_data)
        num_train_batches = len(train_dataloader)
        avg_batch_time = np.mean(batch_times) if batch_times else 0
        samples_per_sec = num_train_samples / train_time if train_time > 0 else 0
        batches_per_sec = num_train_batches / train_time if train_time > 0 else 0

        if is_main_process():
            speed_log['epoch'].append(epoch_num)
            speed_log['epoch_time_s'].append(epoch_time)
            speed_log['train_time_s'].append(train_time)
            speed_log['val_time_s'].append(val_time)
            speed_log['samples_per_sec'].append(samples_per_sec)
            speed_log['batches_per_sec'].append(batches_per_sec)
            speed_log['avg_batch_time_ms'].append(avg_batch_time * 1000)

            current_lr = scheduler.get_last_lr()[0]
            print(f'Epoch: {epoch_num+1}/{epochs} | LR: {current_lr:.2e} | '
                  f'Train Loss: {total_loss_train / len(train_data):.3f} | '
                  f'Train {metric_name}: {train_metric**0.5:.3f} | '
                  f'Val Loss: {total_loss_val / len(val_data):.3f} | '
                  f'Val {metric_name}: {val_metric**0.5:.3f}')

            if use_cuda and epoch_memory_samples:
                avg_alloc = np.mean([s['allocated'] for s in epoch_memory_samples])
                peak_alloc = max([s['max_allocated'] for s in epoch_memory_samples])
                print(f'GPU Memory - Avg: {avg_alloc:.2f} MB, Peak: {peak_alloc:.2f} MB')

            print(f'Time: {epoch_time:.1f}s (Train {train_time:.1f}s + Val {val_time:.1f}s) | '
                  f'Speed: {samples_per_sec:.2f} samples/s, {avg_batch_time*1000:.1f} ms/batch')

        is_best = (val_metric < best_val_metric) if task == 'regression' else (val_metric > best_val_metric)
        if is_best:
            best_val_metric = val_metric

        save_checkpoint(model, optimizer, scheduler, epoch_num, best_val_metric, checkpoint_dir, is_best=is_best)

        if is_main_process():
            if use_cuda and memory_log['epoch']:
                pd.DataFrame(memory_log).to_csv(os.path.join(checkpoint_dir, 'gpu_memory_log.csv'), index=False)
            if speed_log['epoch']:
                pd.DataFrame(speed_log).to_csv(os.path.join(checkpoint_dir, 'training_speed_log.csv'), index=False)

        if world_size > 1:
            dist.barrier()

    if is_main_process():
        if use_cuda and memory_log['epoch']:
            pd.DataFrame(memory_log).to_csv(os.path.join(checkpoint_dir, 'gpu_memory_log.csv'), index=False)
            print(f"GPU memory log saved.")
            final_alloc, final_reserved, peak_alloc = get_gpu_memory_info(device)
            print(f"Final GPU Memory - Allocated: {final_alloc:.2f} MB, Peak: {peak_alloc:.2f} MB")

        if speed_log['epoch']:
            pd.DataFrame(speed_log).to_csv(os.path.join(checkpoint_dir, 'training_speed_log.csv'), index=False)
            print(f"Speed log saved.")
            print(f"Avg Epoch: {np.mean(speed_log['epoch_time_s']):.2f}s | "
                  f"Avg Throughput: {np.mean(speed_log['samples_per_sec']):.2f} samples/s | "
                  f"Avg Batch: {np.mean(speed_log['avg_batch_time_ms']):.2f} ms")

    return best_val_metric


def evaluate(model, test_data, tokenizer, rank=0, local_rank=0, world_size=1, task='regression'):
    test_dataset = Dataset(test_data, tokenizer)

    if world_size > 1:
        test_sampler = DistributedSampler(test_dataset, num_replicas=world_size, rank=rank, shuffle=False)
        test_dataloader = torch.utils.data.DataLoader(
            test_dataset, batch_size=2, sampler=test_sampler, pin_memory=True, num_workers=2
        )
    else:
        test_dataloader = torch.utils.data.DataLoader(test_dataset, batch_size=2)

    use_cuda = torch.cuda.is_available()
    device = torch.device(f"cuda:{rank}" if use_cuda else "cpu")

    if use_cuda:
        model = model.to(device)

    model.eval()
    total_metric_test = 0
    criterion = nn.MSELoss() if task == 'regression' else None
    metric_name = 'RMSE' if task == 'regression' else 'Accuracy'

    with torch.no_grad():
        for test_input, test_label in test_dataloader:
            test_label = test_label.to(device)
            mask = test_input['attention_mask'].squeeze(1).to(device)
            input_id = test_input['input_ids'].squeeze(1).to(device)

            output = model(input_id, mask)

            if task == 'regression':
                mse = criterion(output.squeeze(), test_label.float())
                total_metric_test += mse.item() * test_label.size(0)
            else:
                total_metric_test += (output.argmax(dim=1) == test_label).sum().item()

    if world_size > 1:
        metric_tensor = torch.tensor([total_metric_test], device=device)
        dist.all_reduce(metric_tensor, op=dist.ReduceOp.SUM)
        total_metric_test = metric_tensor.item()

    if is_main_process():
        test_metric = total_metric_test / len(test_data)
        print(f'Test {metric_name}: {test_metric**0.5:.3f}')


if __name__ == "__main__":
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    np.random.seed(112)

    rank, world_size, local_rank = setup_distributed()
    torch.manual_seed(112 + rank)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(112 + rank)

    parser = argparse.ArgumentParser(
        description='Classical LLM Fine-tuning',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--input', default='./Molten_Salt_Thermophysical_Properties.csv', help='input csv file')
    parser.add_argument('--model', default='/lustre/orion/world-shared/stf218/junqi/forge/forge-s4', help='huggingface model')
    parser.add_argument('--emb-size', default=2064, type=int, help='embedding size')
    parser.add_argument('--seq-len', default=128, type=int, help='sequence length')
    parser.add_argument('--batch-size', default=4, type=int, help='batch size')
    parser.add_argument('--num-epochs', default=100, type=int, help='number of epochs')
    parser.add_argument('--freeze-llm', action='store_true', help='freeze LLM weights (only train head)')
    parser.add_argument('--use-differential-lr', action='store_true', help='use lower LR for LLM, higher for head')
    parser.add_argument('--checkpoint-dir', default='./checkpoints', help='directory to save checkpoints')
    parser.add_argument('--resume-from', default=None, help='path to checkpoint to resume from')
    parser.add_argument('--eval-only', action='store_true', help='only run evaluation, no training')
    parser.add_argument('--task', default='regression', choices=['classification', 'regression'], help='task type')
    parser.add_argument('--n-outputs', default=1, type=int, help='number of outputs (classes or regression dims)')
    parser.add_argument('--warmup-epochs', default=0, type=int, help='number of warmup epochs')
    parser.add_argument('--min-lr', default=1e-7, type=float, help='minimum learning rate')
    parser.add_argument('--initial-lr', default=None, type=float, help='initial learning rate (default 1e-5)')
    args = parser.parse_args()

    if 'bert' in args.model.lower():
        from transformers import BertTokenizer
        tokenizer = BertTokenizer.from_pretrained(args.model)
    elif 'forge' in args.model.lower():
        from transformers import GPTNeoXTokenizerFast
        tokenizer = GPTNeoXTokenizerFast.from_pretrained(args.model)
        tokenizer.padding_side = "left"
        tokenizer.pad_token = tokenizer.eos_token

    df = pd.read_csv(args.input)

    if args.task == "classification":
        df_train_val, df_test = train_test_split(df, test_size=0.10, stratify=df["space-group"], random_state=42)
        df_train, df_val = train_test_split(df_train_val, test_size=0.111, stratify=df_train_val["space-group"], random_state=42)
    else:
        df = df[pd.to_numeric(df['melt'], errors='coerce').notna()]
        df["processed"] = df.apply(lambda row: prepare_llm_input(row["formula"], row["comp"]), axis=1)

        from hybrid_split import hybrid_split_salt_data
        df_train, df_val, df_test = hybrid_split_salt_data(
            df,
            formula_col='formula',
            test_formula_frac=0,
            val_formula_frac=0.15,
            val_composition_frac=0.15,
            min_train_compositions=2,
            random_state=42
        )

    if is_main_process():
        print(f"Train: {len(df_train)}, Val: {len(df_val)}, Test: {len(df_test)}")

    EPOCHS = args.num_epochs
    LR = args.initial_lr if args.initial_lr is not None else 1e-5

    model = ClassicalGPT(
        args.model,
        emb_size=args.emb_size,
        seq_len=args.seq_len,
        n_outputs=args.n_outputs,
        freeze_llm=args.freeze_llm,
        task=args.task,
    )

    if world_size > 1:
        device = torch.device(f"cuda:{local_rank}")
        model = model.to(device)
        model = DDP(model, device_ids=[local_rank], output_device=local_rank)
        if is_main_process():
            print("Model wrapped in DistributedDataParallel")

    if args.eval_only:
        if not args.resume_from:
            print("Error: --eval-only requires --resume-from <checkpoint_path>")
            cleanup_distributed()
            exit(1)

        device = torch.device(f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu")
        load_checkpoint(model, None, None, args.resume_from, device)

        if is_main_process():
            print("Running evaluation only...")

        evaluate(model, df_val, tokenizer, rank=rank, local_rank=local_rank, world_size=world_size, task=args.task)
        cleanup_distributed()
        exit(0)

    best_val_metric = train(
        model, df_train, df_val, tokenizer, LR, EPOCHS,
        batch_size=args.batch_size,
        use_differential_lr=args.use_differential_lr,
        rank=rank, local_rank=local_rank, world_size=world_size,
        checkpoint_dir=args.checkpoint_dir,
        resume_from=args.resume_from,
        task=args.task,
        warmup_epochs=args.warmup_epochs,
        min_lr=args.min_lr
    )

    if is_main_process():
        model_to_save = model.module if hasattr(model, 'module') else model
        model_name = f"{args.model.split('/')[-1]}_classical"
        torch.save(model_to_save, f"{model_name}_classifier.pt")
        print(f"Model saved to {model_name}_classifier.pt")
        metric_name = 'RMSE' if args.task == 'regression' else 'Accuracy'
        print(f"Best validation {metric_name}: {best_val_metric**0.5:.3f}")

    if world_size > 1:
        dist.barrier()

    evaluate(model, df_val, tokenizer, rank=rank, local_rank=local_rank, world_size=world_size, task=args.task)

    cleanup_distributed()

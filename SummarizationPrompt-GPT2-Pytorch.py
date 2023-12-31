# -*- coding: utf-8 -*-
"""1.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1nA1Tn1f4Qir9P9wJoJQfG3RlpvAul2aZ
"""

import torch
from torch.utils.data import DataLoader, TensorDataset
from transformers import GPT2LMHeadModel, GPT2Tokenizer
import pandas as pd

# Constants
MODEL_NAME = "gpt2"
BATCH_SIZE = 1
EPOCHS = 10
PROMPT_TOKEN = "[SUMMARIZE]"
MAX_LEN = 1024

# Soft Prompt Vocabulary
soft_prompt_vocab = ["[SUMMARIZE]"]  # Define your custom vocabulary here

# Create a word2idx dictionary for the soft prompt vocabulary
soft_prompt_word2idx = {word: idx for idx, word in enumerate(soft_prompt_vocab)}

num_prompts = len([soft_prompt_word2idx[word] for word in PROMPT_TOKEN.split()])
prompt_id = torch.tensor([soft_prompt_word2idx[word] for word in PROMPT_TOKEN.split()])

# Model Architecture
class GPT2WithSoftPrompt(torch.nn.Module):
    def __init__(self, model_name, num_prompts, embedding_size=768):
        super().__init__()
        self.gpt2 = GPT2LMHeadModel.from_pretrained(model_name)
        self.soft_prompt = torch.nn.Embedding(num_prompts, embedding_size)

    def forward(self, input_ids, prompt_ids):
        prompt_embeddings = self.soft_prompt(prompt_ids)
        base_embeddings = self.gpt2.transformer.wte(input_ids)
        embeddings = torch.cat([prompt_embeddings, base_embeddings.squeeze(0)], dim=0)
        outputs = self.gpt2(inputs_embeds=embeddings)
        return outputs

# Data Loading and Preprocessing
def load_and_preprocess_data(file_path, num_prompts):
    df = pd.read_csv(file_path)
    df = df.dropna().sample(frac=0.001)  # Use only 10% of the data

    # Perform preprocessing on the data
    tokenized_articles = []
    tokenized_summaries = []

    tokenizer = GPT2Tokenizer.from_pretrained(MODEL_NAME)

    for article, summary in zip(df["article"], df["highlights"]):
        # Adjust the maximum length of articles to avoid exceeding MAX_LEN
        max_length_article = MAX_LEN - num_prompts
        article_tokens = tokenizer.encode(article, truncation=True, max_length=max_length_article)
        summary_tokens = tokenizer.encode(summary, truncation=True, max_length=300)

        max_length_summary = MAX_LEN
        padded_article = article_tokens + [tokenizer.eos_token_id] * (max_length_article - len(article_tokens))
        padded_summary = summary_tokens + [tokenizer.eos_token_id] * (max_length_summary - len(summary_tokens))

        tokenized_articles.append(padded_article)
        tokenized_summaries.append(padded_summary)


    return tokenized_articles, tokenized_summaries


# Load and preprocess the data
tokenizer = GPT2Tokenizer.from_pretrained(MODEL_NAME)

tokenized_articles_train,tokenized_summaries_train = load_and_preprocess_data("cnn_dailymail/train.csv", num_prompts)
tokenized_articles_validation,tokenized_summaries_validation = load_and_preprocess_data("cnn_dailymail/validation.csv", num_prompts)
tokenized_articles_test,tokenized_summaries_test = load_and_preprocess_data("cnn_dailymail/test.csv", num_prompts)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# # Model Initialization
model = GPT2WithSoftPrompt(MODEL_NAME, num_prompts).to(device)

from tqdm import tqdm

# Hyperparameters
BATCH_SIZE = 1
EPOCHS = 10
GRADIENT_ACCUMULATION_STEPS = 1
GRADIENT_CLIP_NORM = 1.0
EARLY_STOPPING_PATIENCE = 2
prompt_id = prompt_id.to(device)
# Import cross_entropy_loss
from torch.nn import CrossEntropyLoss

def fine_tune_on_summarization(model, train_articles, train_summaries, val_articles, val_summaries, test_articles, test_summaries):
    optimizer = torch.optim.Adam(model.soft_prompt.parameters())

    best_val_loss = float('inf')
    no_improvement_epochs = 0

    for epoch in range(EPOCHS):
        model.train()

        # Gradient accumulation initialization
        optimizer.zero_grad()
        accumulated_loss = 0
        loss = 0
        # Use tqdm for progress bar
        with tqdm(enumerate(zip(train_articles, train_summaries)), total=len(train_articles), desc=f"Epoch {epoch + 1}/{EPOCHS}", unit="batch") as progress:
            train_percentage_matched = 0
            train_percentage_matched_ct = 0
            for idx, (article, summary) in progress:
                input_ids = torch.tensor(article).to(device)
                labels = torch.tensor(summary).to(device)
                outputs = model(input_ids, prompt_id)

                ignore_index = tokenizer.eos_token_id
                loss += CrossEntropyLoss(ignore_index=ignore_index)(outputs.logits, labels)

                # Metrics
                set1 = set(torch.argmax(outputs.logits, dim=1).cpu().numpy())
                set2 = set(labels.cpu().numpy())

                # Calculate the intersection of sets
                intersection = set1.intersection(set2)

                # Calculate the percentage of indices in the first tensor that are also in the second tensor
                percentage = (len(intersection) / len(set1)) * 100
                train_percentage_matched += percentage
                train_percentage_matched_ct += 1

                # Backpropagate losses every GRADIENT_ACCUMULATION_STEPS or at the end of the dataset
                if (idx + 1) % GRADIENT_ACCUMULATION_STEPS == 0 or idx == len(train_articles) - 1:
                    (loss / GRADIENT_ACCUMULATION_STEPS).backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), GRADIENT_CLIP_NORM)
                    optimizer.step()
                    optimizer.zero_grad()
                    loss = 0

            print("Train : % Exact Match: ",train_percentage_matched/train_percentage_matched_ct)

        # Validation
        model.eval()
        total_val_loss = 0
        with torch.no_grad():
            val_percentage_matched = 0
            val_percentage_matched_ct = 0
            for article, summary in tqdm(zip(val_articles, val_summaries), total=len(val_articles), desc="Validation", unit="batch"):
                input_ids = torch.tensor(article).to(device)
                labels = torch.tensor(summary).to(device)
                outputs = model(input_ids, prompt_id)

                ignore_index = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else -100
                val_loss = CrossEntropyLoss(ignore_index=ignore_index)(outputs.logits, labels)
                total_val_loss += val_loss.item()

                # Metrics
                set1 = set(torch.argmax(outputs.logits, dim=1).cpu().numpy())
                set2 = set(labels.cpu().numpy())

                # Calculate the intersection of sets
                intersection = set1.intersection(set2)

                # Calculate the percentage of indices in the first tensor that are also in the second tensor
                percentage = (len(intersection) / len(set1)) * 100
                val_percentage_matched += percentage
                val_percentage_matched_ct += 1

        print("Val : % Exact Match: ",val_percentage_matched/val_percentage_matched_ct)
        avg_val_loss = total_val_loss / len(val_articles)
        print("Val Loss : ",avg_val_loss)

        # Early stopping
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            no_improvement_epochs = 0
        else:
            no_improvement_epochs += 1
            if no_improvement_epochs >= EARLY_STOPPING_PATIENCE:
                print(f"Early stopping after {EARLY_STOPPING_PATIENCE} epochs without improvement.")
                break


    # Testing
    model.eval()
    total_test_loss = 0
    with torch.no_grad():
        test_percentage_matched = 0
        test_percentage_matched_ct = 0
        for article, summary in tqdm(zip(test_articles, test_summaries), total=len(test_articles), desc="Test", unit="batch"):
            input_ids = torch.tensor(article).to(device)
            labels = torch.tensor(summary).to(device)
            outputs = model(input_ids, prompt_id)

            ignore_index = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else -100
            test_loss = CrossEntropyLoss(ignore_index=ignore_index)(outputs.logits, labels)
            total_test_loss += test_loss.item()

            # Metrics
            set1 = set(torch.argmax(outputs.logits, dim=1).cpu().numpy())
            set2 = set(labels.cpu().numpy())

            # Calculate the intersection of sets
            intersection = set1.intersection(set2)

            # Calculate the percentage of indices in the first tensor that are also in the second tensor
            percentage = (len(intersection) / len(set1)) * 100
            test_percentage_matched += percentage
            test_percentage_matched_ct += 1


        print("Test : % Exact Match: ",test_percentage_matched/test_percentage_matched_ct)
        avg_test_loss = total_test_loss / len(test_articles)
        print("Test Loss : ",avg_test_loss)


    return model

fine_tuned_model = fine_tune_on_summarization(model, tokenized_articles_train, tokenized_summaries_train, tokenized_articles_validation, tokenized_summaries_validation, tokenized_articles_test,tokenized_summaries_test)

"""# Saving Model"""

# Save the fine-tuned model
torch.save(fine_tuned_model.state_dict(), '1.pth')

"""# Loading Model"""

# Initialize a new instance of the model
model = GPT2WithSoftPrompt(MODEL_NAME, num_prompts).to(device)

# Load the saved model state_dict
model.load_state_dict(torch.load('1.pth'))

# Make sure the model is in evaluation mode after loading
model.eval()

"""# Inference"""

# Set the model to evaluation mode
model.eval()

# Input text for summarization
input_text = "Sally Forrest, an actress-dancer who graced the silver screen throughout the '40s and '50s in MGM musicals and films such as the 1956 noir While the City Sleeps died on March 15 at her home in Beverly Hills, California. Forrest, whose birth name was Katherine Feeney, was 86 and had long battled cancer. Her publicist, Judith Goffin, announced the news Thursday. Scroll down for video . Actress: Sally Forrest was in the 1951 Ida Lupino-directed film 'Hard, Fast and Beautiful' (left) and the 1956 Fritz Lang movie 'While the City Sleeps' A San Diego native, Forrest became a protege of Hollywood trailblazer Ida Lupino, who cast her in starring roles in films including the critical and commercial success Not Wanted, Never Fear and Hard, Fast and Beautiful. Some of Forrest's other film credits included Bannerline, Son of Sinbad, and Excuse My Dust, according to her iMDB page. The page also indicates Forrest was in multiple Climax! and Rawhide television episodes. Forrest appeared as herself in an episode of The Ed Sullivan Show and three episodes of The Dinah Shore Chevy Show, her iMDB page says. She also starred in a Broadway production of The Seven Year Itch. City News Service reported that other stage credits included As You Like It, No, No, Nanette and Damn Yankees. Forrest married writer-producer Milo Frank in 1951. He died in 2004. She is survived by her niece, Sharon Durham, and nephews, Michael and Mark Feeney. Career: A San Diego native, Forrest became a protege of Hollywood trailblazer Ida Lupino, who cast her in starring roles in films ."

# Tokenize and encode the input text
input_ids = tokenizer.encode(input_text, truncation=True, max_length=1024)

# Convert the input_ids to a PyTorch tensor
input_ids = torch.tensor(input_ids)

# Generate a summary
with torch.no_grad():
    # Assuming single prompt
    outputs = model(input_ids.to(device), prompt_ids=prompt_id.to(device))
    pred_logits = outputs.logits
    print(pred_logits.shape)


# Get the token IDs with the highest probability for each position
predicted_token_ids = torch.argmax(pred_logits, dim=-1)

# Convert token IDs into words using the tokenizer
predicted_tokens = tokenizer.decode(predicted_token_ids.squeeze(0), skip_special_tokens=True)

predicted_tokens

# Set the model to evaluation mode
model.eval()

# Input text for summarization
input_text = "Celtic defender Virgil van Dijk admits he feared his cup final dream had been wrecked by last week's red card at Tannadice. The Dutchman was sent-off during the Hoops' William Hill Scottish Cup quarter-final draw with Dundee United following an off-the-ball spat with the Taysider's Calum Butcher. Referee Craig Thompson sent-off Van Dijk before making a major gaffe when he wrongly dismissed Butcher's Terrors team-mate Paul Paton. Virgil van Dijk (2nd left) goes up for a header during Celtic's 2-0 Scottish League Cup final win . Hoops boss Ronny Deila celebrates winning his first piece of silverware since arriving at Parkhead . That left both men sweating on their places for Sunday's QTS Scottish League Cup showdown at Hampden. But there was relief for Van Dijk and Paton as they were later cleared to play after successful appeals to the Scottish Football Association's disciplinary panel. And it was the Celtic centre-half who was left bearing the brightest grin as his side clinched Ronny Deila's first trophy as Parkhead boss with a 2-0 win at the National Stadium. Van Dijk said: 'It was quite a tough week. I've never experienced anything like it in my life. 'It would have been very disappointing if I'd not been allowed to play on Sunday, especially with it being my first final ever. 'But you know, justice was served and I was able to play. Luckily I got the red card overturned and we did a good job.' The Dutch defender was sent off for a clash with Callum Butcher in Scottish Cup quarter finals . Van Dijk is sent off but was later cleared to play on Sunday following a successful appeal . Dundee United's Paul Paton also had his red card rescinded after being wrongly sent off . Van Dijk has been linked with summer moves to south, with Barclays Premier League high-flyers Arsenal and Southampton monitoring his progress. But for now the 23-year-old is happy to enjoy Celtic's treble chase. 'It means a lot to have lifted the League Cup,' he said. 'It's my first cup trophy ever. 'This club is an amazing place, I have always said that. I have been improving since the day came here. 'That is the most important thing for a player. If you win trophies, that's even better.' Kris Commons fired Celtic ahead midway through the first half before substitute James Forrest stroked home a second 12 minutes from time. The Hoops winger also had time to miss a late penalty as a United side that spent the last 35 minutes a man down following skipper Sean Dillon's red card avoided a heavier defeat. However, Jackie McNamara's team will go for revenge when the sides meet for part three of their four-game duel with Wednesday's Scottish Cup replay. Van Dijk said: 'We made it tough for ourselves on Sunday and should have finished the game faster in the second half. James Forrest celebrates scoring Celtic's second goal before missing a late penalty at Hampden . Scorer of Celtic's first goal, Kris Commons, celebrates with the trophy following Celtic's win . 'One-nil is a dangerous score - if they had scored one goal they would have had the believe to hit us with everything. 'At moments it looked tough but Craig Gordon only had one save to make in the first half and nothing in the second half, so I think we did well. 'It's a big boost for us ahead of Wednesday night. We can go for the second cup now full of confidence. 'They will be up for it on Wednesday night and know they have possibilities with the players coming back in to their team. 'But we need to be ready and win the game."

# Tokenize and encode the input text
input_ids = tokenizer.encode(input_text, truncation=True, max_length=1024)

# Convert the input_ids to a PyTorch tensor
input_ids = torch.tensor(input_ids)

# Generate a summary
with torch.no_grad():
    # Assuming single prompt
    outputs = model(input_ids.to(device), prompt_ids=prompt_id.to(device))
    pred_logits = outputs.logits
    print(pred_logits.shape)


# Get the token IDs with the highest probability for each position
predicted_token_ids = torch.argmax(pred_logits, dim=-1)

# Convert token IDs into words using the tokenizer
predicted_tokens = tokenizer.decode(predicted_token_ids.squeeze(0), skip_special_tokens=True)

predicted_tokens

"""# Hard Prompt"""

import torch
from torch.utils.data import DataLoader, TensorDataset
from transformers import GPT2LMHeadModel, GPT2Tokenizer
import pandas as pd
from tqdm import tqdm

# Constants
MODEL_NAME = "gpt2"
BATCH_SIZE = 1
EPOCHS = 1
PROMPT_TOKEN = "Summarize the following sentence :"
MAX_LEN = 1024

# Soft Prompt Vocabulary
soft_prompt_vocab = ["Summarize", "the", "following", "sentence", ":"]  # Define your custom vocabulary here
# Create a word2idx dictionary for the soft prompt vocabulary
soft_prompt_word2idx = {word: idx for idx, word in enumerate(soft_prompt_vocab)}

num_prompts = len([soft_prompt_word2idx[word] for word in PROMPT_TOKEN.split()])
prompt_id = torch.tensor([soft_prompt_word2idx[word] for word in PROMPT_TOKEN.split()])

# Model Architecture
class GPT2WithSoftPrompt(torch.nn.Module):
    def __init__(self, model_name, num_prompts, embedding_size=768):
        super().__init__()
        self.gpt2 = GPT2LMHeadModel.from_pretrained(model_name)
        self.soft_prompt = torch.nn.Embedding(num_prompts, embedding_size)

    def forward(self, input_ids, prompt_ids):
        prompt_embeddings = self.soft_prompt(prompt_ids)
        base_embeddings = self.gpt2.transformer.wte(input_ids)
        embeddings = torch.cat([prompt_embeddings, base_embeddings.squeeze(0)], dim=0)
        outputs = self.gpt2(inputs_embeds=embeddings)
        return outputs

# Data Loading and Preprocessing
def load_and_preprocess_data(file_path, num_prompts):
    df = pd.read_csv(file_path)
    df = df.dropna().sample(frac=0.0001)  # Use only 10% of the data

    # Perform preprocessing on the data
    tokenized_articles = []
    tokenized_summaries = []

    tokenizer = GPT2Tokenizer.from_pretrained(MODEL_NAME)

    for article, summary in zip(df["article"], df["highlights"]):
        # Adjust the maximum length of articles to avoid exceeding MAX_LEN
        max_length_article = MAX_LEN - num_prompts
        article_tokens = tokenizer.encode(article, truncation=True, max_length=max_length_article)
        summary_tokens = tokenizer.encode(summary, truncation=True, max_length=300)

        max_length_summary = MAX_LEN
        padded_article = article_tokens + [tokenizer.eos_token_id] * (max_length_article - len(article_tokens))
        padded_summary = summary_tokens + [tokenizer.eos_token_id] * (max_length_summary - len(summary_tokens))

        tokenized_articles.append(padded_article)
        tokenized_summaries.append(padded_summary)


    return tokenized_articles, tokenized_summaries


# Load and preprocess the data
tokenizer = GPT2Tokenizer.from_pretrained(MODEL_NAME)

tokenized_articles_train,tokenized_summaries_train = load_and_preprocess_data("cnn_dailymail/train.csv", num_prompts)
tokenized_articles_validation,tokenized_summaries_validation = load_and_preprocess_data("cnn_dailymail/validation.csv", num_prompts)
tokenized_articles_test,tokenized_summaries_test = load_and_preprocess_data("cnn_dailymail/test.csv", num_prompts)
device = "cpu"


# # Model Initialization
model = GPT2WithSoftPrompt(MODEL_NAME, num_prompts).to(device)

from torch.nn import CrossEntropyLoss

model.eval()
total_test_loss = 0
with torch.no_grad():
    test_percentage_matched = 0
    test_percentage_matched_ct = 0
    for article, summary in tqdm(zip(tokenized_articles_test, tokenized_summaries_test), total=len(tokenized_articles_test), desc="Test", unit="batch"):
        input_ids = torch.tensor(article).to(device)
        labels = torch.tensor(summary).to(device)
        outputs = model(input_ids, prompt_id)

        ignore_index = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else -100
        test_loss = CrossEntropyLoss(ignore_index=ignore_index)(outputs.logits, labels)
        total_test_loss += test_loss.item()

        # Metrics
        set1 = set(torch.argmax(outputs.logits, dim=1).cpu().numpy())
        set2 = set(labels.cpu().numpy())

        # Calculate the intersection of sets
        intersection = set1.intersection(set2)

        # Calculate the percentage of indices in the first tensor that are also in the second tensor
        percentage = (len(intersection) / len(set1)) * 100
        test_percentage_matched += percentage
        test_percentage_matched_ct += 1


    print("Test : % Exact Match: ",test_percentage_matched/test_percentage_matched_ct)
    avg_test_loss = total_test_loss / len(tokenized_articles_test)
    print("Test Loss : ",avg_test_loss)


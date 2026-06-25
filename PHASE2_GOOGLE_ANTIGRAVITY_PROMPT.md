# PHASE 2 IMPLEMENTATION FOR GOOGLE ANTIGRAVITY

## YOUR PROJECT CONTEXT

**Project Path**: `C:\Users\Christian\Desktop\projects\forex-system\`

**Phase 1 Status**: ✅ Complete
- Data pipeline (data_ingestion.py)
- Features (23 indicators)
- Backtester (walk-forward validation)
- All tests passing

**Phase 2 Goal**: Implement ML models (LSTM + XGBoost + Ensemble)

**Timeline**: 4 weeks, 1 component per week

---

## WEEK 1: LSTM PRICE PREDICTOR

### What to Build
Predict next candle close price using 60-candle lookback window

### File Location
`src/models/lstm_predictor.py`

### Requirements
```
Input: 60 candles × 23 normalized features (from Phase 1)
Output: Predicted close price (float)

Architecture:
- LSTM(128, return_sequences=True) + Dropout(0.2)
- LSTM(64, return_sequences=False) + Dropout(0.2)
- Dense(32, activation='relu')
- Dense(1)  ← output

Training:
- Optimizer: Adam(learning_rate=0.001)
- Loss: MSE
- Epochs: 50
- Batch size: 32
- Validation split: 10%
- Early stopping: patience=5

Target: MAE < 0.05% on test set
```

### Code Structure
```python
class LSTMPredictor:
    def __init__(self, lookback=60)
    def prepare_data(df, features_df, test_size=0.2) → (X_train, y_train), (X_test, y_test)
    def build_model(input_shape) → keras model
    def train(X_train, y_train, epochs=50, batch_size=32) → history
    def predict_next_price(recent_data) → float
    def save(path) → saves model + scaler
    def load(path) → loads model + scaler
```

### Integration
```python
# Use Phase 1 features
from src.features import engineer_features

# Use Phase 1 data
from src.data_ingestion import ForexDataPipeline
```

### Tests Required
- Data preparation (shapes correct)
- Model builds without error
- Training improves loss
- Predictions within range
- Save/load works

### Acceptance Criteria
- ✅ MAE < 0.05%
- ✅ Can predict from 60-candle window
- ✅ Model persists correctly
- ✅ No Phase 1 breaking changes

---

## WEEK 2: XGBOOST SIGNAL GENERATOR

### What to Build
Generate trading signals: 1=UP, 0=FLAT, -1=DOWN

### File Location
`src/models/xgboost_classifier.py`

### Requirements
```
Input: 23 normalized features
Output: Ternary classification (1, 0, -1)

Labels (5-candle lookahead):
- 1: close will rise > 0.5%
- -1: close will fall < -0.5%
- 0: flat (between -0.5% and +0.5%)

Validation: 5-fold stratified cross-validation
Target accuracy: > 55% (better than 33% random)

XGBoost Config:
- objective: 'multi:softmax'
- num_class: 3
- max_depth: 6
- learning_rate: 0.1
- n_estimators: 200
- subsample: 0.8
- colsample_bytree: 0.8
```

### Code Structure
```python
class XGBoostSignal:
    def __init__(self)
    def prepare_labels(df, lookahead=5, threshold_pct=0.005) → labels
    def train(X, y, cv_folds=5) → cv_scores
    def predict_signal(X) → int (1, 0, or -1)
    def predict_proba(X) → array (confidence scores)
    def feature_importance() → top 10 features
    def save(path)
    def load(path)
```

### Tests Required
- Label generation (correct distribution)
- Cross-validation runs
- Accuracy > 55%
- Feature importance calculated
- Save/load works

### Acceptance Criteria
- ✅ CV accuracy > 55%
- ✅ Precision > 0.5 all classes
- ✅ Feature importance identified
- ✅ Signal generation working

---

## WEEK 3: ENSEMBLE STRATEGY

### What to Build
Combine LSTM + XGBoost with confidence filtering

### File Location
`src/models/ensemble.py`

### Requirements
```
Combines:
1. LSTM price prediction → signal direction
2. XGBoost signal prediction → signal direction
3. Alignment check → only trade if both agree
4. Confidence threshold → filter low-confidence signals

Signal generation:
- LSTM: 1 if pred > current, -1 if pred < current
- XGBoost: 1, 0, or -1 from classifier
- Ensemble: 1 or -1 only if signals align
- Confidence: average of LSTM strength + XGBoost confidence
- Filter: only signal if confidence > threshold (default 0.65)

Walk-forward validation:
- Train on 252 days (1 year)
- Test on 63 days (3 months)
- Rolling windows
- Report metrics each period
```

### Code Structure
```python
class EnsembleStrategy:
    def __init__(self, lstm_predictor, xgboost_signal, threshold_confidence=0.65)
    def generate_signal(recent_data, features) → (signal, confidence)
    def backtest_ensemble(df, features_df, backtester) → walk_forward_results
    def run_walk_forward(df, features_df) → DataFrame with results
```

### Integration
```python
# Use Phase 1 backtester
from src.backtester import Backtester

# Walk-forward produces:
# - Sharpe ratio
# - Max drawdown
# - Win rate
# - Profit factor
# - Total P&L
```

### Tests Required
- Signal generation (return format correct)
- Alignment logic (only trade when both agree)
- Confidence filtering works
- Walk-forward completes
- Backtest integration works
- Metrics reasonable (Sharpe > 0.5)

### Acceptance Criteria
- ✅ Sharpe > 0.5 (target > 1.0)
- ✅ Win rate > 45% (target > 50%)
- ✅ Max drawdown < 30%
- ✅ Walk-forward validation passing
- ✅ Ready for Phase 3

---

## FILE STRUCTURE TO CREATE

```
src/models/
├── __init__.py
├── lstm_predictor.py          Week 1
├── xgboost_classifier.py      Week 2
└── ensemble.py                Week 3

tests/
├── test_lstm.py               Week 1
├── test_xgboost.py            Week 2
└── test_ensemble.py           Week 3
```

---

## DEPENDENCIES TO ADD

```
tensorflow==2.13.0
xgboost==1.7.6
scikit-learn==1.3.0
numpy==1.24.3
pandas==2.0.3
```

---

## DATA TO USE

```python
from src.data_ingestion import ForexDataPipeline
from src.features import engineer_features

# Load 1yr+ data
pipeline = ForexDataPipeline()
df = pipeline.get_ohlcv('EURUSD', timeframe=240, limit=2000)
# or from CSV:
df = pipeline.fetch_historical_data_csv('EURUSD', 240)

# Generate features
features, engine = engineer_features(df, normalize=True)

# Now ready for ML
```

---

## TESTING TARGETS

| Component | Metric | Target |
|-----------|--------|--------|
| LSTM | MAE | < 0.05% |
| XGBoost | CV Accuracy | > 55% |
| Ensemble | Sharpe | > 0.5 |
| Ensemble | Win Rate | > 45% |
| Ensemble | Max DD | < 30% |

---

## OUTPUT DELIVERABLES

**Week 1:**
- ✅ src/models/lstm_predictor.py
- ✅ tests/test_lstm.py
- ✅ Trained model saved
- ✅ All tests passing

**Week 2:**
- ✅ src/models/xgboost_classifier.py
- ✅ tests/test_xgboost.py
- ✅ Feature importance analysis
- ✅ CV accuracy > 55%

**Week 3:**
- ✅ src/models/ensemble.py
- ✅ tests/test_ensemble.py
- ✅ Walk-forward results
- ✅ Backtest report
- ✅ Ready for Phase 3

---

## CRITICAL REQUIREMENTS

### Code Quality
- Full docstrings on every method
- Type hints: `def method(param: str) → int:`
- Error handling for edge cases
- Logging with logger

### Data Integrity
- No data leakage (don't use test labels to train)
- Normalize each fold independently
- Set seeds for reproducibility: `np.random.seed(42)`

### Integration
- Import Phase 1 code: `from src.features import engineer_features`
- Use Phase 1 backtester: `from src.backtester import Backtester`
- Don't modify Phase 1 files
- Maintain same config: `from config.config import SYMBOLS`

---

## EXPECTED RESULTS

After Phase 2:
```
✅ LSTM predicting prices with < 0.05% error
✅ XGBoost classifying signals with > 55% accuracy
✅ Ensemble combining signals intelligently
✅ Walk-forward validation showing > 0.5 Sharpe
✅ All code tested and documented
✅ Models saving/loading correctly
✅ Ready to deploy Phase 3
```

---

## TIMELINE

- **Week 1**: LSTM implementation + testing
- **Week 2**: XGBoost implementation + testing
- **Week 3**: Ensemble + walk-forward validation
- **Week 4**: Refinement + documentation + final testing

---

## SUCCESS CHECKLIST

Before moving to Phase 3:

```
Week 1:
☐ LSTM model created
☐ Tests written
☐ MAE < 0.05%
☐ Model saves/loads

Week 2:
☐ XGBoost model created
☐ CV accuracy > 55%
☐ Feature importance analyzed
☐ Tests passing

Week 3:
☐ Ensemble created
☐ Walk-forward completed
☐ Sharpe > 0.5
☐ All tests passing
☐ Documentation complete
☐ Phase 1 integration verified
```

---

## HOW TO USE WITH GOOGLE ANTIGRAVITY

1. **Copy this entire prompt** (Ctrl+A → Ctrl+C)
2. **Open Google Antigravity**
3. **Paste the prompt** into your project
4. **Ask Antigravity to implement Week 1:**
   ```
   "Implement Week 1 from this Phase 2 spec:
   - Create src/models/lstm_predictor.py
   - Create tests/test_lstm.py
   - Show example usage and expected output"
   ```
5. **Antigravity writes the code**
6. **Copy code to your project**
7. **Run tests locally**
8. **Repeat for Weeks 2 and 3**

---

## NOTES FOR GOOGLE ANTIGRAVITY

- This is Week 1, 2, or 3 (ask for one at a time)
- Use existing Phase 1 code (data_ingestion.py, features.py, backtester.py)
- Don't break Phase 1 - keep it intact
- Full production-quality code (not stub/pseudocode)
- Include all error handling and logging
- Write comprehensive tests
- Show example usage with real data
- Target the specific metrics (MAE, accuracy, Sharpe)

---

**Ready to implement Phase 2? Give this prompt to Google Antigravity! 🚀**

#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import cryptocode
from datetime import datetime, tzinfo
from dateutil.tz import tz
from hashlib import sha256
import json
import math
import requests
import sqlite3
import time
import yaml

import traceback

from classes.terra_instance import TerraInstance

from constants.constants import (
    BASE_SMART_CONTRACT_ADDRESS,
    CHAIN_DATA,
    UBASE,
    ULUNA,
    UUSD
)

from terra_classic_sdk.client.lcd import LCDClient
from terra_classic_sdk.client.lcd.api.distribution import Rewards
from terra_classic_sdk.client.lcd.api.tx import (
    CreateTxOptions,
    Tx
)
from terra_classic_sdk.client.lcd.params import PaginationOptions
from terra_classic_sdk.client.lcd.wallet import Wallet
from terra_classic_sdk.core.bank import MsgSend
from terra_classic_sdk.core.broadcast import BlockTxBroadcastResult
from terra_classic_sdk.core.coin import Coin
from terra_classic_sdk.core.coins import Coins
from terra_classic_sdk.core.distribution.msgs import MsgWithdrawDelegatorReward
from terra_classic_sdk.core.fee import Fee
from terra_classic_sdk.core.ibc import Height
from terra_classic_sdk.core.ibc_transfer import MsgTransfer
from terra_classic_sdk.core.market.msgs import MsgSwap
from terra_classic_sdk.core.osmosis import MsgSwapExactAmountIn, Pool, PoolAsset
from terra_classic_sdk.core.staking import (
    MsgBeginRedelegate,
    MsgDelegate,
    MsgUndelegate,
    UnbondingDelegation
)
from terra_classic_sdk.core.staking.data.delegation import Delegation
from terra_classic_sdk.core.staking.data.validator import Validator
from terra_classic_sdk.core.tx import Tx
from terra_classic_sdk.core.wasm.msgs import MsgExecuteContract
from terra_classic_sdk.exceptions import LCDResponseError
from terra_classic_sdk.key.mnemonic import MnemonicKey

from .transaction_core import TransactionCore

class SendTransaction(TransactionCore):
    def __init__(self, *args, **kwargs):

        super(SendTransaction, self).__init__(*args, **kwargs)

        self.amount:int            = 0
        self.block_height:int      = None
        self.denom:str             = ''
        self.fee:Fee               = None
        self.fee_deductables:float = None
        self.gas_limit:str         = 'auto'
        self.is_on_chain:bool      = True
        self.memo:str              = ''
        self.recipient_address:str = ''
        self.recipient_prefix:str  = ''
        self.revision_number:int   = None
        self.sender_address:str    = ''
        self.sender_prefix:str     = ''
        self.source_channel:str    = None
        self.tax:float             = None

    def create(self, denom:str = 'uluna'):
        """
        Create a send object and set it up with the provided details.
        """

        # Create the terra instance
        self.terra = TerraInstance().create(denom)

        # Create the wallet based on the calculated key
        prefix              = CHAIN_DATA[denom]['prefix']
        current_wallet_key  = MnemonicKey(mnemonic = self.seed, prefix = prefix)
        self.current_wallet = self.terra.wallet(current_wallet_key)

        # Get the gas prices and tax rate:
        self.gas_list = self.gasList()
        self.tax_rate = self.taxRate()

        return self

    def send(self) -> bool:
        """
        Complete a send transaction with the information we have so far.
        If fee is None then it will be a simulation.
        The fee denomination must be uluna - it is the only one we are supporting.
        """

        send_amount = int(self.amount)

        if self.fee_deductables is not None:
            if int(send_amount + self.tax) > int(self.balances[self.denom]):
                send_amount = int(send_amount - self.fee_deductables)

        try:
            tx:Tx = None

            if self.denom == UBASE:
                msg = MsgExecuteContract(
                    sender      = self.current_wallet.key.acc_address,
                    contract    = BASE_SMART_CONTRACT_ADDRESS,
                    execute_msg = {
                        "transfer": {
                            "amount": str(send_amount),
                            "recipient": self.recipient_address
                        }
                    }
                )
            else:
                msg = MsgSend(
                    from_address = self.current_wallet.key.acc_address,
                    to_address   = self.recipient_address,
                    amount       = Coins(str(int(send_amount)) + self.denom)
                )

            options = CreateTxOptions(
                account_number = self.account_number,
                fee            = self.fee,
                fee_denoms     = ['uluna'],
                gas            = str(self.gas_limit),
                gas_prices     = self.gas_list,
                memo           = self.memo,
                msgs           = [msg],
                sequence       = self.sequence
            )

            # This process often generates sequence errors. If we get a response error, then
            # bump up the sequence number by one and try again.
            while True:
                try:
                    tx:Tx = self.current_wallet.create_and_sign_tx(options)
                    break
                except LCDResponseError as err:
                    if 'account sequence mismatch' in err.message:
                        self.sequence    = self.sequence + 1
                        options.sequence = self.sequence
                        print (' 🛎️  Boosting sequence number')
                    else:
                        print ('An unexpected error occurred in the send function:')
                        print (err)
                        break
                except Exception as err:
                    print (' 🛑 An unexpected error occurred in the send function:')
                    print (err)
                    break

            # Store the transaction
            self.transaction = tx

            return True
        
        except Exception as err:
            print (' 🛑 An unexpected error occurred in the send function:')
            print (err)
            return False
        
    def sendOffchain(self) -> bool:
        """
        Complete a send transaction with the information we have so far.
        If fee is None then it will be a simulation.
        """

        send_amount = int(self.amount)

        if self.tax is not None:
            if self.fee_deductables is not None:
                if int(send_amount + self.tax) > int(self.balances[self.denom]):
                    send_amount = int(send_amount - self.fee_deductables)

        try:
            tx:Tx = None

            if self.sender_prefix == 'terra':
                msg = MsgTransfer(
                    source_port       = 'transfer',
                    source_channel    = self.source_channel,
                    token             = Coin(self.denom, send_amount),
                    sender            = self.sender_address,
                    receiver          = self.recipient_address,
                    timeout_height    = Height(revision_number = self.revision_number, revision_height = self.block_height),                            
                    timeout_timestamp = 0
                )
                
                options = CreateTxOptions(
                    fee        = self.fee,
                    gas        = self.gas_limit,
                    gas_prices = self.gas_list,
                    memo       = self.memo,
                    msgs       = [msg],
                    sequence   = self.sequence
                )
            else:
                # Send transactions from Osmosis to other wallets
                ibc_value = sha256(f'transfer/{self.source_channel}/{self.denom}'.encode('utf-8')).hexdigest().upper()
                send_denom = 'ibc/' + ibc_value

                if self.fee is not None:
                    print (self.fee)
                    exit()

                msg = MsgTransfer(
                    source_port       = 'transfer',
                    source_channel    = self.source_channel,
                    token = {
                        "amount": str(send_amount),
                        "denom": send_denom
                    },
                    sender            = self.sender_address,
                    receiver          = self.recipient_address,
                    timeout_height    = Height(revision_number = 6, revision_height = self.block_height),
                    timeout_timestamp = 0
                )
                                    
                options = CreateTxOptions(
                    account_number = str(self.account_number),
                    sequence       = str(self.sequence),
                    msgs           = [msg],
                    fee            = self.fee,
                    #gas            = '7500',
                    gas = self.gas_limit,
                    fee_denoms     = ['uosmo', 'uluna']
                )

            # This process often generates sequence errors. If we get a response error, then
            # bump up the sequence number by one and try again.
            while True:
                try:
                    tx:Tx = self.current_wallet.create_and_sign_tx(options)
                    break
                except LCDResponseError as err:
                    if 'account sequence mismatch' in err.message:
                        self.sequence    = self.sequence + 1
                        options.sequence = self.sequence
                        print (' 🛎️  Boosting sequence number')
                    else:
                        print ('An unexpected error occurred in the send function:')
                        print (err)
                        break
                except Exception as err:
                    print (' 🛑 An unexpected error occurred in the send function:')
                    print (err)
                    break

            # Store the transaction
            self.transaction = tx

            return True
        except Exception as err:
            print (' 🛑 An unexpected error occurred in the send function:')
            print (err)
            return False
    
    def simulate(self) -> bool:
        """
        Simulate a delegation so we can get the fee details.

        Outputs:
        self.fee - requested_fee object with fee + tax as separate coins (unless both are lunc)
        self.tax - the tax component
        self.fee_deductables - the amount we need to deduct off the transferred amount
        """

        # Reset these values in case this is a re-used object:
        self.account_number  = self.current_wallet.account_number()
        self.fee             = None
        self.fee_deductables = None
        self.tax             = None
        self.sequence        = self.current_wallet.sequence()

        # Perform the swap as a simulation, with no fee details
        self.send()

        # Store the transaction
        tx:Tx = self.transaction

        if tx is not None:
            # Get the stub of the requested fee so we can adjust it
            requested_fee:Fee = tx.auth_info.fee

            # This will be used by the swap function next time we call it
            # We'll use uluna as the preferred fee currency just to keep things simple
            self.fee = self.calculateFee(requested_fee, ULUNA)
            
            # Figure out the fee structure
            fee_bit:Coin = Coin.from_str(str(requested_fee.amount))
            fee_amount   = fee_bit.amount
            fee_denom    = fee_bit.denom
        
            # Calculate the tax portion
            if self.denom == UBASE:
                # No taxes for BASE transfers
                self.tax = 0
            else:
                self.tax = int(math.ceil(self.amount * float(self.tax_rate['tax_rate'])))

            # Build a fee object
            if fee_denom == ULUNA and self.denom == ULUNA:
                new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount + self.tax))})
            elif self.denom == UBASE:
                new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount))})
            else:
                new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount)), Coin(self.denom, int(self.tax))})
                
            requested_fee.amount = new_coin
            
            # This will be used by the swap function next time we call it
            self.fee = requested_fee
        
            # Fee deductibles are the total cost of this transaction.
            # This assumes that the tax is always the same denom as the transferred amount.
            if self.tax > 0:
                if fee_denom == self.denom:
                    # If the fee denom is the same as what we're paying the tax in, then combine the two
                    self.fee_deductables = int(fee_amount + self.tax)
                elif fee_denom == ULUNA and self.denom == UUSD:
                    # If this is UUSD transfer then the deductible is just the tax
                    self.fee_deductables = int(self.tax)
                else:
                    # Everything else incurs a 2x tax (@TODO give exmaples)
                    self.fee_deductables = int(self.tax * 2)

            return True
        else:
            return False
        
    def simulateOffchain(self) -> bool:
        """
        Simulate a delegation so we can get the fee details.
        The fee details are saved so the actual delegation will work.

        Outputs:
        self.fee - requested_fee object with fee + tax as separate coins (unless both are lunc)
        self.tax - the tax component
        self.fee_deductables - the amount we need to deduct off the transferred amount
        """

        if self.sequence is None:
            self.sequence = self.current_wallet.sequence()

        if self.account_number is None:
            self.account_number = self.current_wallet.account_number()

        # Perform the swap as a simulation, with no fee details
        self.send()

        # Store the transaction
        tx:Tx = self.transaction

        if tx is not None:
            # Get the stub of the requested fee so we can adjust it
            requested_fee:Fee = tx.auth_info.fee

            # This will be used by the swap function next time we call it
            # We'll use uluna as the preferred fee currency just to keep things simple
            self.fee = self.calculateFee(requested_fee, ULUNA)
            
            # Figure out the fee structure
            fee_bit:Coin = Coin.from_str(str(requested_fee.amount))
            fee_amount   = fee_bit.amount
            fee_denom    = fee_bit.denom
        
            if self.is_ibc_transfer == False:
                
                # Calculate the tax portion
                if self.denom == UBASE:
                    # No taxes for BASE transfers
                    self.tax = 0
                else:
                    self.tax = int(math.ceil(self.amount * float(self.tax_rate['tax_rate'])))

                # Build a fee object
                if fee_denom == ULUNA and self.denom == ULUNA:
                    new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount + self.tax))})
                elif self.denom == UBASE:
                    new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount))})
                else:
                    new_coin:Coins = Coins({Coin(fee_denom, int(fee_amount)), Coin(self.denom, int(self.tax))})
                    
                requested_fee.amount = new_coin
            else:
                # No taxes for IBC transfers
                self.tax = 0

            # This will be used by the swap function next time we call it
            self.fee = requested_fee
        
            # Store this so we can deduct it off the total amount to swap.
            # If the fee denom is the same as what we're paying the tax in, then combine the two
            # Otherwise the deductible is just the tax value
            # This assumes that the tax is always the same denom as the transferred amount.
            if fee_denom == self.denom:
                self.fee_deductables = int(fee_amount + self.tax)
            elif fee_denom == ULUNA and self.denom == UUSD:
                self.fee_deductables = int(self.tax)
            else:
                self.fee_deductables = int(self.tax * 2)

            return True
        else:
            return False
        
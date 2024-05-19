#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

from classes.common import (
    check_database,
    check_version
)

from constants.constants import (
    FULL_COIN_LOOKUP,
    ULUNA,
    USER_ACTION_QUIT
)

from classes.swap_transaction import swap_coins
from classes.transaction_core import TransactionResult
from classes.wallet import UserParameters
from classes.wallets import UserWallet, UserWallets

from terra_classic_sdk.core.coin import Coin

def main():

    # Check if there is a new version we should be using
    check_version()
    check_database()

    # Get the user wallets
    wallets = UserWallets()
    user_wallets = wallets.loadUserWallets()

    if user_wallets is None:  
        print ("\n 🛑 This password couldn't decrypt any wallets. Make sure it is correct, or rebuild the wallet list by running the configure_user_wallet.py script again.\n")
        exit()

    if len(user_wallets) > 0:
        print (f'You can make swaps on the following wallets:')

        wallet:UserWallet
        wallet, answer = wallets.getUserSinglechoice("Select a wallet number 1 - " + str(len(user_wallets)) + ", 'X' to continue, or 'Q' to quit: ")

        if answer == USER_ACTION_QUIT:
            print ('\n 🛑 Exiting...\n')
            exit()
    else:
        print ("\n 🛑 This password couldn't decrypt any wallets. Make sure it is correct, or rebuild the wallet list by running the configure_user_wallet.py script again.\n")
        exit()

    # Swaps use LUNC as the denom - if there is no LUNC in the wallet, then exit
    if ULUNA not in wallet.balances:
        print ('\nYou need LUNC in this wallet to pay for swap transactions.')
        print ('Please transfer some LUNC to this address before swapping.')
        print ('\n 🛑 Exiting...\n')
        exit()

    # List all the coins in this wallet, with the amounts available:
    print ('\nWhat coin do you want to swap FROM?')
    coin_count:int = 0
    for coin in wallet.balances:
        if coin in FULL_COIN_LOOKUP:
            coin_count += 1

    coin_from, answer, _ = wallet.getCoinSelection("Select a coin number 1 - " + str(coin_count) + ", 'X' to continue, or 'Q' to quit: ", wallet.balances)

    if answer == USER_ACTION_QUIT:
        print ('\n 🛑 Exiting...\n')
        exit()

    available_balance:float = wallet.formatUluna(wallet.balances[coin_from], coin_from)
    print (f'This coin has a maximum of {available_balance} {FULL_COIN_LOOKUP[coin_from]} available.')

    user_params:UserParameters      = UserParameters()
    user_params.max_number          = float(available_balance)
    user_params.percentages_allowed = True
    user_params.target_amount       = wallet.formatUluna(wallet.balances[coin_from], coin_from)
    user_params.target_denom        = coin_from

    swap_uluna = wallet.getUserNumber("How much do you want to swap? (Or type 'Q' to quit) ", user_params)

    if swap_uluna == USER_ACTION_QUIT:
        print ('\n 🛑 Exiting...\n')
        exit()
    else:
        swap_uluna = float(swap_uluna)                   

    print ('\nWhat coin do you want to swap TO?')
    coin_to, answer, estimated_amount = wallet.getCoinSelection("Select a coin number 1 - " + str(len(FULL_COIN_LOOKUP)) + ", 'X' to continue, or 'Q' to quit: ", wallet.balances, False, {'denom':coin_from, 'amount':swap_uluna})

    if answer == USER_ACTION_QUIT:
        print ('\n 🛑 Exiting...\n')
        exit()

    estimated_amount = str(("%.6f" % (estimated_amount)).rstrip('0').rstrip('.'))

    swap_coin:Coin = wallet.createCoin(swap_uluna, coin_from)

    transaction_result:TransactionResult = swap_coins(wallet, swap_coin, coin_to, estimated_amount, False, True)
    
    transaction_result.showResults()

    print ('\n 💯 Done!\n')

if __name__ == "__main__":
    """ This is executed when run from the command line """
    main()